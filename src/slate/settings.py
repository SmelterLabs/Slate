from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value can be set as SLATE_<NAME> in the environment."""

    model_config = SettingsConfigDict(env_prefix="SLATE_", env_file=".env", extra="ignore")

    database_path: Path = Path("data/slate.db")
    timezone: str = "UTC"
    static_dir: Path | None = None  # built board; None = not served
    scheduler_enabled: bool = True
    scheduler_interval_seconds: float = 60.0
    # The automations engine (slate.automator). Separate from the scheduler so the board's e2e
    # tests can run rules live while recurrence and due events stay off.
    automations_enabled: bool = True
    # Phone and desktop notifications (slate.pusher). Also needs secret_key (VAPID is derived).
    push_enabled: bool = True
    # Webhooks. The key encrypts subscription secrets at rest: 32+ random bytes,
    # base64 or hex. Unset outside tests means webhooks refuse to start (see slate.crypto).
    secret_key: str | None = None
    webhook_timeout_seconds: float = 10.0
    # Delay before each retry; len() is the number of retries after the first attempt.
    webhook_retry_seconds: list[float] = [10, 60, 300, 1800, 7200]
    webhook_concurrency: int = 8
    # Finished webhook delivery rows (ok, failed, skipped, retry) older than this are pruned
    # by the dispatcher (service/deliveries.py `prune`); queued rows are never pruned. 0 keeps
    # the whole delivery log for ever.
    delivery_retention_days: int = 30
    # Where webhooks and automation `webhook` actions may post. Private targets (loopback,
    # LAN and tailnet ranges, link-local, bare names, .local and friends) are refused unless
    # this is true, both when a URL is saved and again when it is resolved at send time:
    # otherwise any write token could probe the services next to Slate. Turn it on when a
    # receiver runs on the same host or network.
    webhook_private_targets: bool = False
    # Hosts a browser's Web Push endpoint may be on (glob patterns). "*" allows any https
    # host whose addresses are public (private ones need webhook_private_targets); the
    # default is the push services of the common browsers.
    push_endpoint_hosts: list[str] = [
        "fcm.googleapis.com",
        "*.push.apple.com",
        "*.push.services.mozilla.com",
        "*.notify.windows.com",
        "*.push.samsungosp.com",
    ]
    # The VAPID contact push services see (a mailto: or https: URL). Empty = public_url when it
    # is https, else a placeholder (slate.webpush `subject`).
    push_subject: str = ""
    # Per-token rate limits (slate.ratelimit): requests per minute, refilled continuously, with
    # a burst of the same size. 0 turns one off; rate_limit_enabled=false turns both off.
    rate_limit_enabled: bool = True
    rate_limit_reads_per_minute: int = 1200
    rate_limit_writes_per_minute: int = 300
    # Items stay in the trash this many days, then the scheduler deletes them for good
    # (their events stay). 0 keeps the trash until someone empties it.
    trash_days: int = 30
    # The Health page (slate.monitor, service/health.py): a call at least this slow counts as
    # slow, and a background poller that fails this many runs in a row raises an alert.
    health_slow_ms: float = 1000.0
    health_alert_after: int = 3
    # URL schemes an agent may use for its `url` and `thread.url` (the badge's Open link). Add
    # a desktop app's scheme (`["http","https","myapp"]`) so a card can open the agent's own
    # chat window; the board reads the list from `GET /me` and trusts no other scheme.
    link_schemes: list[str] = ["http", "https"]
    # How many inflated bytes the server will read out of one uploaded file to index its text
    # for search and compare (a DOCX or XLSX part after unzipping, a PDF's content streams
    # after FlateDecode). An upload over it still succeeds and is stored and viewable; it is
    # only not indexed (`has_text` false). Generous, so a real spreadsheet never trips it:
    # the cap exists for a 1 MB file that inflates to 300 MB.
    doc_extract_max_bytes: int = 64 * 1024 * 1024
    # AI add (service/aiadd.py): an Ollama server Slate asks to fill in a task described in
    # plain words. Empty = off (the board hides the switch). Typically the host's own local
    # model, so nothing leaves the host and nothing is billed.
    ai_url: str = ""
    ai_model: str = "gemma4:latest"
    ai_timeout: float = 120.0
    # Email notifications (slate.emailer, service/email.py): any SMTP server. Port 465 is
    # implicit TLS, anything else uses STARTTLS when the server offers it. Empty host = off
    # (the board hides the Email column). `public_url` is the board's address, so a link in
    # an email opens the right place (e.g. https://slate.example.com).
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    public_url: str = ""

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                "timezone must be a valid IANA timezone (e.g. America/New_York)"
            ) from None
        return value

    @property
    def database_url(self) -> str:
        if str(self.database_path) == ":memory:":
            return "sqlite+pysqlite:///:memory:"
        return f"sqlite+pysqlite:///{self.database_path.as_posix()}"


settings = Settings()
