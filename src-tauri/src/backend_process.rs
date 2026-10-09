use std::{
    collections::VecDeque,
    io,
    process::{Child, Command},
    time::{Duration, Instant},
};

const RESTART_WINDOW: Duration = Duration::from_secs(60);
const RESTART_LIMIT: usize = 3;

/// Own the sidecar and its original launch configuration. Recovery happens only
/// after the child has exited, so an unresponsive service is never duplicated.
pub(crate) struct BackendProcess {
    child: Child,
    command: Command,
    restarts: VecDeque<Instant>,
}

impl BackendProcess {
    pub(crate) fn start(mut command: Command) -> io::Result<Self> {
        let child = command.spawn()?;
        Ok(Self {
            child,
            command,
            restarts: VecDeque::new(),
        })
    }

    pub(crate) fn ensure_running(&mut self) -> Result<(), String> {
        self.ensure_running_at(Instant::now())
    }

    fn ensure_running_at(&mut self, now: Instant) -> Result<(), String> {
        let Some(status) = self
            .child
            .try_wait()
            .map_err(|_| "Cannot inspect the local service")?
        else {
            return Ok(());
        };
        while self
            .restarts
            .front()
            .is_some_and(|started| now.duration_since(*started) >= RESTART_WINDOW)
        {
            self.restarts.pop_front();
        }
        if self.restarts.len() >= RESTART_LIMIT {
            return Err(format!(
                "The local service keeps exiting ({status}). Wait a minute and retry, or inspect Meridian's local logs."
            ));
        }
        // Failed launches count too, so polling cannot cause an unbounded loop.
        self.restarts.push_back(now);
        self.child = self.command.spawn().map_err(|_| {
            "The local service could not restart. Retry or inspect Meridian's local logs."
                .to_string()
        })?;
        Ok(())
    }

    pub(crate) fn shutdown(&mut self) {
        // A previous status check may already have reaped this PID. Never signal
        // a reaped child: the OS may have assigned its PID to another process.
        if !matches!(self.child.try_wait(), Ok(None)) {
            return;
        }
        #[cfg(unix)]
        unsafe {
            libc::kill(self.child.id() as libc::pid_t, libc::SIGTERM);
        }
        #[cfg(not(unix))]
        let _ = self.child.kill();
        for _ in 0..50 {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::process::Stdio;

    fn command(script: &str) -> Command {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", script]).stdin(Stdio::null());
        command
    }

    #[test]
    fn recovers_exited_child_without_replacing_running_child() {
        let mut process = BackendProcess::start(command("exec sleep 30")).unwrap();
        let original = process.child.id();
        process.ensure_running().unwrap();
        assert_eq!(process.child.id(), original);
        process.child.kill().unwrap();
        process.child.wait().unwrap();
        process.ensure_running().unwrap();
        assert_ne!(process.child.id(), original);
        assert!(process.child.try_wait().unwrap().is_none());
        process.shutdown();
        assert!(process.child.try_wait().unwrap().is_some());
    }

    #[test]
    fn crash_loop_is_bounded_and_can_be_retried_after_a_minute() {
        let mut process = BackendProcess::start(command("exit 7")).unwrap();
        let now = Instant::now();
        for _ in 0..RESTART_LIMIT {
            process.child.wait().unwrap();
            process.ensure_running_at(now).unwrap();
        }
        process.child.wait().unwrap();
        assert!(process
            .ensure_running_at(now)
            .unwrap_err()
            .contains("Wait a minute"));
        process.ensure_running_at(now + RESTART_WINDOW).unwrap();
        process.child.wait().unwrap();
        process.shutdown();
    }

    #[test]
    fn failed_restart_attempts_also_count_towards_the_limit() {
        let mut process = BackendProcess::start(command("exit 0")).unwrap();
        process.child.wait().unwrap();
        process.command = Command::new("/nonexistent/meridian-backend-test");
        let now = Instant::now();
        for _ in 0..RESTART_LIMIT {
            assert!(process
                .ensure_running_at(now)
                .unwrap_err()
                .contains("could not restart"));
        }
        assert!(process
            .ensure_running_at(now)
            .unwrap_err()
            .contains("Wait a minute"));
    }
}
