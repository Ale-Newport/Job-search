#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend_process;

use backend_process::BackendProcess;
use serde::Serialize;
#[cfg(unix)]
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Command, Stdio},
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

#[derive(Serialize)]
struct ChromeTab {
    url: String,
    title: String,
}

fn chrome_read_error(stderr: &[u8]) -> String {
    let diagnostic = String::from_utf8_lossy(stderr);
    if diagnostic.contains("(-1743)") {
        "macOS denied permission to read Chrome. Allow Meridian in System Settings > Privacy & Security > Automation, or paste the application URL.".into()
    } else if diagnostic.contains("(-2741)")
        || diagnostic.contains("(-2740)")
        || diagnostic.contains("SyntaxError")
    {
        "Chrome metadata reader could not parse its script. Paste the application URL and report this reader error.".into()
    } else if diagnostic.contains("(-600)") || diagnostic.contains("Open a job application") {
        "Open a job application in Google Chrome first, or paste its URL.".into()
    } else if diagnostic.contains("No visible Chrome window") {
        "Bring your job application window in Chrome to the front, or paste its URL. Hidden background browser windows are not used.".into()
    } else {
        "Chrome could not return its active tab. Paste the application URL to continue.".into()
    }
}

#[tauri::command]
async fn current_chrome_tab() -> Result<ChromeTab, String> {
    #[cfg(not(target_os = "macos"))]
    return Err("Paste the application URL to start from this browser.".into());
    #[cfg(target_os = "macos")]
    tauri::async_runtime::spawn_blocking(|| {
        // Target the process owning an actual on-screen Chrome window. Headless
        // Chrome can report AppleScript's `visible` as true, and bundle-name
        // addressing can otherwise select a background discovery/test process.
        // Read only native window ownership and the active tab's URL/title;
        // never page JavaScript, pixels, cookies or browser profile files.
        let script = r#"ObjC.import('AppKit');
ObjC.import('CoreGraphics');
var nativeWindows = ObjC.deepUnwrap($.CGWindowListCopyWindowInfo(
    $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, $.kCGNullWindowID)) || [];
var selectedPID = null;
for (var i = 0; i < nativeWindows.length && i < 500; i++) {
    var info = nativeWindows[i];
    if (info.kCGWindowLayer !== 0) continue;
    var running = $.NSRunningApplication.runningApplicationWithProcessIdentifier(info.kCGWindowOwnerPID);
    if (running && ObjC.unwrap(running.bundleIdentifier) === 'com.google.Chrome') {
        selectedPID = info.kCGWindowOwnerPID; break;
    }
}
// On another macOS Space, the last Chrome window may not be in the on-screen
// list. Prefer the primary regular Chrome process over newer automation
// instances. Normal Chrome profiles share that primary browser process.
if (selectedPID === null) {
    var runningApps = $.NSWorkspace.sharedWorkspace.runningApplications;
    var primaryChrome = null;
    var oldestLaunch = Infinity;
    for (var k = 0; k < runningApps.count; k++) {
        var candidate = runningApps.objectAtIndex(k);
        if (ObjC.unwrap(candidate.bundleIdentifier) !== 'com.google.Chrome' ||
            Number(candidate.activationPolicy) !== 0 || candidate.terminated) continue;
        var launched = Number(candidate.launchDate.timeIntervalSince1970);
        if (launched < oldestLaunch) { primaryChrome = Number(candidate.processIdentifier); oldestLaunch = launched; }
    }
    selectedPID = primaryChrome;
}
if (selectedPID === null) throw new Error('No visible Chrome window');
var chrome = Application(selectedPID);
var windows = chrome.windows();
var selectedWindow = null;
for (var j = 0; j < windows.length; j++) {
    if (!windows[j].minimized()) { selectedWindow = windows[j]; break; }
}
if (selectedWindow === null) throw new Error('No visible Chrome window');
var tab = selectedWindow.activeTab();
tab.url() + '\n' + tab.title();"#;
        let mut child = Command::new("/usr/bin/osascript")
            .args(["-l", "JavaScript", "-e", script])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn().map_err(|error| error.to_string())?;
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        loop {
            if child.try_wait().map_err(|error| error.to_string())?.is_some() { break; }
            if std::time::Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Chrome did not respond. Allow Meridian in System Settings > Privacy & Security > Automation, or paste the application URL.".into());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let output = child.wait_with_output().map_err(|error| error.to_string())?;
        if !output.status.success() {
            return Err(chrome_read_error(&output.stderr));
        }
        let text = String::from_utf8(output.stdout).map_err(|_| "Chrome returned invalid text")?;
        let (url, title) = text.trim_end().split_once('\n').unwrap_or((text.trim(), ""));
        if !(url.starts_with("https://") || url.starts_with("http://")) {
            return Err("Select a public job or application page in Chrome.".into());
        }
        Ok(ChromeTab { url: url.into(), title: title.into() })
    }).await.map_err(|error| error.to_string())?
}
struct Backend {
    process: Mutex<BackendProcess>,
    connection: ConnectionInfo,
    port: u16,
    data_root: PathBuf,
}

#[tauri::command]
fn show_review_window(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or("Main window unavailable")?;
    window.show().map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
fn connection_info(backend: tauri::State<'_, Backend>) -> Result<ConnectionInfo, String> {
    backend
        .process
        .lock()
        .map_err(|_| "Backend lock failed")?
        .ensure_running()?;
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

/// `Meridian.app/Contents/MacOS/meridian` becomes `Meridian.app`. `None` when the executable does not live in an
/// application bundle (`tauri dev`, `cargo run`), so the backend never tries to reopen an unrelated installed copy.
fn app_bundle_from_executable(executable: &Path) -> Option<PathBuf> {
    let mut ancestors = executable.ancestors().skip(1);
    let macos = ancestors.next()?;
    let contents = ancestors.next()?;
    let bundle = ancestors.next()?;
    let named = |path: &Path, name: &str| path.file_name().is_some_and(|value| value == name);
    (named(macos, "MacOS")
        && named(contents, "Contents")
        && bundle.extension().is_some_and(|value| value == "app"))
    .then(|| bundle.to_path_buf())
}

/// A `meridian://` URL only brings the window forward. Its path and query are never read: any web page can open one.
#[cfg(target_os = "macos")]
fn is_meridian_url(urls: &[tauri::Url]) -> bool {
    urls.iter().any(|url| url.scheme() == "meridian")
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            let _ = show_review_window(app.clone());
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let root = app.path().app_data_dir()?;
            // Human-readable macOS application support folder, shared with Python.
            let data_root = std::env::var_os("MERIDIAN_DATA_DIR")
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
                .unwrap_or_else(|| root.parent().unwrap_or(&root).join("Meridian"));
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
            command
                .env("MERIDIAN_API_TOKEN", &token)
                .env("MERIDIAN_PORT", port.to_string())
                .env("MERIDIAN_DATA_DIR", &data_root)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::from(log));
            // Release builds tell the backend which bundle to bring forward when the browser extension needs Meridian
            // (`open <bundle>`). Development runs are not in a bundle, and reopening an installed copy there would start
            // it against the real data folder, so the variable is cleared even if the shell had one.
            let bundle = if cfg!(debug_assertions) {
                None
            } else {
                std::env::current_exe()
                    .ok()
                    .and_then(|exe| app_bundle_from_executable(&exe))
            };
            match bundle {
                Some(bundle) => command.env("MERIDIAN_APP_BUNDLE", bundle),
                None => command.env_remove("MERIDIAN_APP_BUNDLE"),
            };
            let process = BackendProcess::start(command)?;
            app.manage(Backend {
                process: Mutex::new(process),
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
                        let _ = show_review_window(app.clone());
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
        .invoke_handler(tauri::generate_handler![
            connection_info,
            export_file,
            show_review_window,
            current_chrome_tab
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("Could not start Meridian");
    app.run(|handle, event| match event {
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => {
            let _ = show_review_window(handle.clone());
        }
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Opened { urls } => {
            if is_meridian_url(&urls) {
                let _ = show_review_window(handle.clone());
            }
        }
        tauri::RunEvent::Exit => {
            if let Some(backend) = handle.try_state::<Backend>() {
                if let Ok(mut process) = backend.process.lock() {
                    process.shutdown();
                }
            }
        }
        _ => {}
    });
}

#[cfg(all(test, unix))]
mod export_tests {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test]
    fn chrome_errors_distinguish_permission_and_reader_failures_without_echoing_page_data() {
        assert!(chrome_read_error(b"Not authorized (-1743)").contains("macOS denied permission"));
        assert!(chrome_read_error(b"Expected end of line (-2741)").contains("parse its script"));
        assert!(chrome_read_error(b"Application isn't running (-600)")
            .contains("Open a job application"));
        assert!(
            chrome_read_error(b"No visible Chrome window (-2700)").contains("Hidden background")
        );
        assert!(!chrome_read_error(b"secret fixture page title").contains("secret"));
    }

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

#[cfg(test)]
mod bundle_tests {
    use super::*;

    #[test]
    fn bundle_is_found_only_for_the_macos_layout_of_an_app() {
        assert_eq!(
            app_bundle_from_executable(Path::new(
                "/Applications/Meridian.app/Contents/MacOS/meridian"
            )),
            Some(PathBuf::from("/Applications/Meridian.app"))
        );
        assert_eq!(
            app_bundle_from_executable(Path::new(
                "/Users/me/My Apps/Meridian.app/Contents/MacOS/meridian"
            )),
            Some(PathBuf::from("/Users/me/My Apps/Meridian.app"))
        );
        // Development and test runs, helper tools and relocated binaries are not an installed app.
        for executable in [
            "/Users/me/Job-search/src-tauri/target/debug/meridian",
            "/Users/me/Job-search/src-tauri/target/release/meridian",
            "/Applications/Meridian.app/Contents/Resources/meridian",
            "/Applications/Meridian/Contents/MacOS/meridian",
            "/Applications/Meridian.app/MacOS/meridian",
            "/meridian",
            "meridian",
        ] {
            assert_eq!(
                app_bundle_from_executable(Path::new(executable)),
                None,
                "{executable}"
            );
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn only_the_meridian_scheme_raises_the_window_and_its_contents_are_irrelevant() {
        let parse = |text: &str| tauri::Url::parse(text).unwrap();
        assert!(is_meridian_url(&[parse("meridian://open")]));
        assert!(is_meridian_url(&[parse(
            "MERIDIAN://anything?next=https://example.com/steal#x"
        )]));
        assert!(is_meridian_url(&[
            parse("https://example.com"),
            parse("meridian://open")
        ]));
        assert!(!is_meridian_url(&[]));
        assert!(!is_meridian_url(&[parse("https://meridian.example/open")]));
        assert!(!is_meridian_url(&[parse("meridian-evil://open")]));
        assert!(!is_meridian_url(&[parse(
            "file:///Applications/Meridian.app"
        )]));
    }
}
