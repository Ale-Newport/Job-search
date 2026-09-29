#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
#[cfg(unix)]
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::Duration,
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};
use tauri_plugin_dialog::DialogExt;

#[derive(Clone, Serialize)]
struct ConnectionInfo {
    base_url: String,
    token: String,
}
struct Backend {
    child: Mutex<Child>,
    connection: ConnectionInfo,
    port: u16,
    data_root: PathBuf,
}

#[tauri::command]
fn connection_info(backend: tauri::State<'_, Backend>) -> Result<ConnectionInfo, String> {
    if let Some(status) = backend
        .child
        .lock()
        .map_err(|_| "Backend lock failed")?
        .try_wait()
        .map_err(|_| "Cannot inspect backend")?
    {
        return Err(format!(
            "The local service exited ({status}). Restart Meridian and inspect its local logs."
        ));
    }
    Ok(backend.connection.clone())
}

fn export_source(source: &Path, data_root: &Path) -> Result<(File, PathBuf), String> {
    let root = data_root
        .canonicalize()
        .map_err(|_| "The local document vault is unavailable")?;
    let metadata = std::fs::symlink_metadata(source)
        .map_err(|_| "The requested export file no longer exists")?;
    if !metadata.file_type().is_file() {
        return Err(
            "Only regular vault files may be exported; symbolic links are not permitted".into(),
        );
    }
    let canonical = source
        .canonicalize()
        .map_err(|_| "Cannot resolve the requested export file")?;
    if !canonical.starts_with(root.join("documents"))
        && !canonical.starts_with(root.join("backups"))
    {
        return Err("Only files in the document or backup vault may be exported".into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);
    let file = options
        .open(&canonical)
        .map_err(|_| "Cannot open the requested export file")?;
    if !file
        .metadata()
        .map_err(|_| "Cannot inspect the export file")?
        .is_file()
    {
        return Err("The export source is not a regular file".into());
    }
    Ok((file, root))
}

fn export_destination(destination: &Path, data_root: &Path) -> Result<File, String> {
    let parent = destination
        .parent()
        .ok_or("Choose a local destination folder")?
        .canonicalize()
        .map_err(|_| "The chosen destination folder is unavailable")?;
    let filename = destination
        .file_name()
        .ok_or("Choose a valid destination filename")?;
    let canonical = parent.join(filename);
    if canonical.starts_with(data_root) {
        return Err("Export outside Meridian's data folder to protect the original files".into());
    }
    if let Ok(metadata) = std::fs::symlink_metadata(&canonical) {
        if !metadata.file_type().is_file() {
            return Err(
                "The destination must be a regular file, not a symbolic link or directory".into(),
            );
        }
        #[cfg(unix)]
        if metadata.nlink() > 1 {
            return Err("Choose a destination that is not a hard link to another file".into());
        }
    }
    let mut options = OpenOptions::new();
    // Do not truncate until metadata is checked on the actual opened file.
    options.write(true).create(true).truncate(false);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);
    let file = options
        .open(&canonical)
        .map_err(|_| "Cannot write to the chosen destination")?;
    let metadata = file
        .metadata()
        .map_err(|_| "Cannot inspect the chosen destination")?;
    if !metadata.is_file() {
        return Err("The destination is not a regular file".into());
    }
    #[cfg(unix)]
    if metadata.nlink() > 1 {
        return Err("Choose a destination that is not a hard link to another file".into());
    }
    file.set_len(0)
        .map_err(|_| "Cannot replace the chosen destination file")?;
    Ok(file)
}

#[tauri::command]
async fn export_file(
    app: tauri::AppHandle,
    backend: tauri::State<'_, Backend>,
    source: String,
    name: String,
) -> Result<bool, String> {
    let data_root = backend.data_root.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let (mut input, root) = export_source(Path::new(&source), &data_root)?;
        let filename = Path::new(&name)
            .file_name()
            .and_then(|value| value.to_str())
            .filter(|value| !value.is_empty() && !value.chars().any(char::is_control))
            .ok_or("The export filename is invalid")?;
        // The blocking native dialog runs off the GUI thread. No frontend fs or
        // dialog permissions are granted; only this restricted IPC command exists.
        let Some(selected) = app
            .dialog()
            .file()
            .set_title("Export from Meridian")
            .set_file_name(filename)
            .blocking_save_file()
        else {
            return Ok(false);
        };
        let destination = selected
            .into_path()
            .map_err(|_| "Choose a local filesystem destination")?;
        let mut output = export_destination(&destination, &root)?;
        std::io::copy(&mut input, &mut output)
            .map_err(|error| format!("Could not finish exporting the file: {error}"))?;
        output
            .sync_all()
            .map_err(|error| format!("Could not save the exported file: {error}"))?;
        Ok(true)
    })
    .await
    .map_err(|_| "The native export task could not complete".to_string())?
}

fn local_action(backend: &Backend, path: &str) {
    if let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", backend.port).parse().unwrap(),
        Duration::from_secs(2),
    ) {
        let body = "{}";
        let request = format!("POST /api/{path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", backend.port, backend.connection.token, body.len());
        let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
        let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
        let _ = stream.write_all(request.as_bytes());
        let mut buffer = [0; 128];
        let _ = stream.read(&mut buffer);
    }
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let root = app.path().app_data_dir()?;
            // Human-readable macOS application support folder, shared with Python.
            let data_root = root.parent().unwrap_or(&root).join("Meridian");
            std::fs::create_dir_all(data_root.join("logs"))?;
            let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
            let token = format!(
                "{}{}",
                uuid::Uuid::new_v4().simple(),
                uuid::Uuid::new_v4().simple()
            );
            let mut command;
            if cfg!(debug_assertions) {
                let project = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap();
                command = Command::new(project.join(".venv/bin/python"));
                command.args(["-m", "jobagent.main"]).current_dir(project);
            } else {
                let executable = std::env::current_exe()?
                    .parent()
                    .unwrap()
                    .join("meridian-backend");
                command = Command::new(executable);
            }
            let log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(data_root.join("logs/backend.log"))?;
            let child = command
                .env("MERIDIAN_API_TOKEN", &token)
                .env("MERIDIAN_PORT", port.to_string())
                .env("MERIDIAN_DATA_DIR", &data_root)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::from(log))
                .spawn()?;
            app.manage(Backend {
                child: Mutex::new(child),
                connection: ConnectionInfo {
                    base_url: format!("http://127.0.0.1:{port}/api"),
                    token,
                },
                port,
                data_root,
            });
            let open = MenuItem::with_id(app, "open", "Open Meridian", true, None::<&str>)?;
            let search = MenuItem::with_id(app, "search", "Run search", true, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "Pause automation", true, None::<&str>)?;
            let resume = MenuItem::with_id(app, "resume", "Resume automation", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit Meridian", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &search, &pause, &resume, &quit])?;
            let tray = TrayIconBuilder::new()
                .icon(tauri::image::Image::new(
                    include_bytes!("../icons/tray-icon.rgba"),
                    44,
                    44,
                ))
                .icon_as_template(true)
                .tooltip("Meridian · private career workspace")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    action => {
                        let path = match action {
                            "search" => "sources/run",
                            "pause" => "automation/pause",
                            "resume" => "automation/resume",
                            _ => return,
                        };
                        let handle = app.clone();
                        std::thread::spawn(move || {
                            local_action(&handle.state::<Backend>(), path);
                        });
                    }
                });
            tray.build(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![connection_info, export_file])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("Could not start Meridian");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(backend) = handle.try_state::<Backend>() {
                if let Ok(mut child) = backend.child.lock() {
                    let _ = Command::new("/bin/kill")
                        .args(["-TERM", &child.id().to_string()])
                        .status();
                    for _ in 0..50 {
                        if child.try_wait().ok().flatten().is_some() {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(100));
                    }
                    if child.try_wait().ok().flatten().is_none() {
                        let _ = child.kill();
                    }
                    let _ = child.wait();
                }
            }
        }
    });
}

#[cfg(all(test, unix))]
mod export_tests {
    use super::*;
    use std::os::unix::fs::symlink;

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let path =
                std::env::temp_dir().join(format!("meridian-export-{}", uuid::Uuid::new_v4()));
            std::fs::create_dir_all(path.join("vault/documents")).unwrap();
            std::fs::create_dir_all(path.join("vault/backups")).unwrap();
            std::fs::create_dir_all(path.join("downloads")).unwrap();
            std::fs::write(
                path.join("vault/documents/resume.pdf"),
                b"verified fixture bytes",
            )
            .unwrap();
            Self(path)
        }

        fn root(&self) -> PathBuf {
            self.0.join("vault").canonicalize().unwrap()
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn export_copies_exact_vault_bytes_to_chosen_external_file() {
        let fixture = Fixture::new();
        let (mut input, root) = export_source(
            &fixture.root().join("documents/resume.pdf"),
            &fixture.root(),
        )
        .unwrap();
        let target = fixture.0.join("downloads/Alex CV.pdf");
        let mut output = export_destination(&target, &root).unwrap();
        std::io::copy(&mut input, &mut output).unwrap();
        output.sync_all().unwrap();
        assert_eq!(std::fs::read(target).unwrap(), b"verified fixture bytes");
    }

    #[test]
    fn export_rejects_outside_and_symlinked_sources() {
        let fixture = Fixture::new();
        let outside = fixture.0.join("downloads/outside.txt");
        std::fs::write(&outside, b"not an export").unwrap();
        assert!(export_source(&outside, &fixture.root()).is_err());
        let link = fixture.root().join("documents/linked.txt");
        symlink(&outside, &link).unwrap();
        assert!(export_source(&link, &fixture.root()).is_err());
        let directory_link = fixture.root().join("documents/external");
        symlink(fixture.0.join("downloads"), &directory_link).unwrap();
        assert!(export_source(&directory_link.join("outside.txt"), &fixture.root()).is_err());
        assert!(export_source(&fixture.root().join("documents"), &fixture.root()).is_err());
    }

    #[test]
    fn export_cannot_overwrite_the_vault_through_links() {
        let fixture = Fixture::new();
        let source = fixture.root().join("documents/resume.pdf");
        assert!(export_destination(&source, &fixture.root()).is_err());
        let link = fixture.0.join("downloads/symlink.pdf");
        symlink(&source, &link).unwrap();
        assert!(export_destination(&link, &fixture.root()).is_err());
        let hardlink = fixture.0.join("downloads/hardlink.pdf");
        std::fs::hard_link(&source, &hardlink).unwrap();
        assert!(export_destination(&hardlink, &fixture.root()).is_err());
        let directory_link = fixture.0.join("downloads/vault-link");
        symlink(fixture.root().join("backups"), &directory_link).unwrap();
        assert!(
            export_destination(&directory_link.join("new-export.pdf"), &fixture.root()).is_err()
        );
        assert_eq!(std::fs::read(source).unwrap(), b"verified fixture bytes");
    }
}
