"""Application configuration. Every secret comes from the environment; nothing is hard-coded."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    # --- App -------------------------------------------------------------
    app_name: str = "TripAI"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    default_timezone: str = "Asia/Kolkata"
    default_currency: str = "INR"

    # --- Database --------------------------------------------------------
    database_url: str = "postgresql+psycopg://tripai:tripai@localhost:5432/tripai"
    db_echo: bool = False

    # --- Auth ------------------------------------------------------------
    jwt_secret: str = "dev-only-change-me-please-32-characters-min"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 24 * 7

    # --- Mode ------------------------------------------------------------
    # auto  -> each provider uses its real integration when its key exists, demo data otherwise
    # true  -> everything uses demo data (no network calls at all)
    # false -> same as auto, but a missing Google key is reported as a startup warning
    demo_mode: Literal["auto", "true", "false"] = "auto"

    # --- LLM -------------------------------------------------------------
    llm_provider: Literal["auto", "openrouter", "openai_compatible", "template"] = "auto"
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Self-hosted OpenAI-compatible server (e.g. vLLM serving Qwen on A100/H100/L40S)
    openai_compat_base_url: str | None = None
    openai_compat_api_key: str | None = None
    openai_compat_model: str = "Qwen/Qwen2.5-32B-Instruct"
    llm_timeout_seconds: float = 45.0
    llm_max_tokens: int = 1800

    # --- Google Maps Platform (server key only; never sent to the browser) ---
    google_maps_server_key: str | None = None

    # --- Indian Railways timetable (third-party, via RapidAPI "IRCTC1") ----
    irctc_rapidapi_key: str | None = None
    irctc_rapidapi_host: str = "irctc1.p.rapidapi.com"
    railradar_api_key: str | None = None

    # --- Web search ------------------------------------------------------
    web_search_provider: Literal["auto", "brave", "serper", "tavily", "none"] = "auto"
    web_search_api_key: str | None = None
    web_verify_max_places: int = 6

    # --- Optional providers ----------------------------------------------
    weather_provider: Literal["auto", "openmeteo", "none"] = "auto"
    hotel_provider: Literal["auto", "places", "estimate"] = "auto"

    # --- Business rules --------------------------------------------------
    free_trip_plans: int = 2
    max_replans_per_trip: int = 40
    payments_mode: Literal["mock", "disabled"] = "mock"
    plus_price_inr: int = 99
    one_trip_price_inr: int = 49

    # --- Sign-up verification (email and mobile codes) --------------------
    # Without SMTP or an SMS provider, codes are printed to the server log and, outside production, returned to the
    # sign-up page so the flow can be tested. In production a missing provider refuses to send instead.
    smtp_host: str | None = None
    smtp_port: int = 587  # 587 = STARTTLS, 465 = SSL
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # e.g. "Yatro <no-reply@yatro.app>"; defaults to smtp_user
    sms_provider: Literal["auto", "twilio", "fast2sms", "console"] = "auto"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from: str | None = None  # a Twilio number (+1...) or a Messaging Service SID (MG...)
    fast2sms_api_key: str | None = None  # Indian numbers only (+91), OTP route
    # Off (the default): sign-up proves the EMAIL with a code, and the mobile number is only collected (unique, +91) for WhatsApp trip
    # sharing later. On: sign-up also needs a code texted to the mobile (needs an SMS provider).
    require_phone_code: bool = False
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_seconds: int = 60  # one text per number a minute (was 30)

    # --- Provenance and retention (Phase 1, ADR 0002) ------------------------
    ai_rail_guidance: bool = False  # source chain link 5; interface only, no implementation wired
    retention_enabled: bool = True  # I6: scrub Google place content older than 30 days, drop expired cache rows

    # --- Two-tier rail storage (Phase 2, ADR 0003) ------------------------------
    rail_storage_sources: str = "railradar"  # only these may write Tier 1 / Tier 2 (I7); IRCTC1 not confirmed
    rail_seasonal_cap: bool = True  # 1 Dec - 28 Feb: saved timetables shown for at most 30 days, not 45
    rail_refresh_daily_cap: int = 10  # background refresh calls per day (0 = off); proper budgets in Phase 4

    # --- Google content in saved trips (ADR 0005) and sharing (ADR 0006) --------
    google_refresh_daily_cap: int = 200  # Place Details re-fetches per day for trips opened after 30 days (0 = off)
    google_refresh_per_trip: int = 20
    google_refresh_budget_seconds: float = 3.0
    share_links_enabled: bool = True
    share_rate_limit_per_minute: int = 30
    upi_links_enabled: bool = True
    public_app_url: str = "http://localhost:3000"  # where share links point (the web app, not the API)

    # --- Provider gateway (Phase 4, ADR 0007) ---------------------------------
    gateway_enabled: bool = True  # off = the old http.request behaviour, exactly
    gateway_breakers: bool = True
    gateway_retry_budget: bool = True
    gateway_bulkheads: bool = True
    gateway_single_flight: bool = False  # interface only until duplicates are measured (see /system/budgets "coalescible")
    gateway_aimd: bool = False  # adaptive bulkhead limits; interface only until measured under load
    gateway_health_routing: bool = False  # order live rail sources by health score; the default order is unchanged
    gateway_timeouts: str | None = None  # JSON overrides: {"<provider> <endpoint>": seconds}
    plan_deadline_seconds: float = 60  # 0 = none. Between the SLO's p95 (45 s) and p99 (90 s)
    breaker_quota_open_minutes: int = 360  # after a quota-exhausted answer, until the single probe call
    irctc1_assume_exhausted: bool = True  # IRCTC1 has no other plan: open until its monthly quota resets, one probe a day

    # Daily budgets. The day resets at midnight in BUDGET_TZ (not UTC): "try again tomorrow" is the user's tomorrow.
    budget_mode: str = "enforce"  # enforce | warn (log only) | off
    budget_tz: str = "Asia/Kolkata"
    budget_flush_seconds: float = 10  # in-process counts are written to daily_counters this often (no per-call write)
    google_daily_usd: float = 10.0
    llm_daily_usd: float = 1.0
    search_daily_usd: float = 1.0
    railradar_daily_calls: int = 30  # the free tier's 1,000 a month / 30, plus a little headroom
    irctc1_daily_calls: int = 10
    # The product limit must sit below what the Google budget can pay for, so people meet the polite daily-cap message
    # before Google is cut off. Worst measured cold plan (New Delhi -> Mussoorie, ADR 0001) is $0.345, rounded up to
    # $0.35: $10 pays for 28 of them; 25 leaves about $1.25 for replans at about $0.05 each.
    daily_plan_cap: int = 25
    cold_plan_worst_usd: float = 0.35
    background_budget_share: float = 0.5  # background work (rail refresh, later pre-warming) may use this much of a day
    busy_notify_email: bool = False  # reserved: "email me when it's back". Email sending doesn't exist yet; never read

    admin_emails: str = ""  # comma-separated; /system/rail-stats and /system/budgets answer 404 to everyone else

    # --- Measurement (Phase 0, ADR 0001) ------------------------------------
    telemetry_enabled: bool | None = None  # None = on outside production
    telemetry_prices: str | None = None  # JSON overrides for the per-call list prices in core/telemetry.py

    # --- Execution and data layer (Phase 5, ADR 0008) ------------------------
    plan_jobs_enabled: bool = True  # false: the old behaviour (a thread per request, no job row, no refund record)
    plan_executor: Literal["inprocess", "arq"] = "inprocess"  # "arq" is an interface only (needs Redis); not wired
    max_concurrent_plans: int = 12  # per process; below the Google bulkhead of 16 (ADR 0007)
    plan_queue_limit: int = 40  # waiting plans past this show "High demand — you're #N"
    plan_queue_hard_limit: int = 100  # waiting plans past this: new free plans are politely refused
    lane_max_wait_seconds: int = 120  # a waiting plan is promoted to the first lane after this long (nobody starves)
    eta_samples: int = 50  # how many recent real plan durations the wait estimate uses
    reaper_enabled: bool = True
    reaper_stale_seconds: int = 180  # a job whose process stopped heartbeating this long ago is refunded (or re-run once)
    job_heartbeat_seconds: int = 15
    job_requeue_max_age_seconds: int = 300  # an interrupted plan younger than this is run again once; older is refunded
    job_retention_days: int = 30
    brownout_level: int = Field(default=0, ge=0, le=4)  # BROWNOUT_LEVEL, also read live from the environment each plan
    brownout_auto: bool = False  # interface only: the suggested level is computed and exported, never applied
    redis_url: str = ""  # unset: everything runs in-process (an optional free container in docker-compose)
    cache_lru_entries: int = 0  # in-process cache tier in front of the database cache; 0 = off
    cache_redis_enabled: bool = False  # Redis cache tier (needs REDIS_URL); off
    cache_ttl_overrides: str = ""  # JSON {"google-routes": seconds, ...}; Google entries are capped at 7 days (I6)
    prewarm_enabled: bool = False
    prewarm_top_n: int = 20
    prewarm_budget_share: float = 0.2  # of the day's RailRadar calls
    db_pool_size: int | None = None  # None: derived from MAX_CONCURRENT_PLANS
    db_max_overflow: int | None = None
    web_concurrency: int = 2
    postgres_max_connections: int = 100  # what the server allows; used for the startup pool warning
    worker_timeout: int = 120
    graceful_timeout: int = 30
    backup_retention_days: int = 14
    plan_test_delay_seconds: float = 0.0  # verification only (VERIFY.md): hold each plan this long after it starts; refused in production

    # --- Client address and verification codes (hotfix 2026-10-07, ADR 0009) --------
    trusted_proxy_ips: str = ""  # comma-separated IPs/CIDRs of OUR OWN proxies; empty = X-Forwarded-For is never believed
    sms_daily_limit: int = 50  # paid text messages a day, in the budget ledger (0 = no cap); the console provider is free and not counted
    otp_per_phone_hour: int = 3
    otp_per_phone_day: int = 5
    otp_per_ip_hour: int = 10
    kill_sms: bool = False  # also read live from the environment (KILL_SMS) on every send

    # --- Protection ------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 120
    rate_limit_plan_per_minute: int = 6
    rate_limit_auth_per_minute: int = 10
    max_body_bytes: int = 256_000
    http_timeout_seconds: float = 12.0

    @field_validator("database_url")
    @classmethod
    def _driver(cls, v: str) -> str:
        """Hosted databases (Neon, Render, Heroku) hand out postgres:// or postgresql:// URLs; the driver installed here is psycopg 3."""
        v = v.strip()
        for plain in ("postgres://", "postgresql://"):
            if v.startswith(plain):
                return "postgresql+psycopg://" + v[len(plain):]
        return v

    @model_validator(mode="after")
    def _check_production(self) -> Settings:
        if self.environment == "production":
            if self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be set to a strong value (32+ chars) in production")
            if self.plan_test_delay_seconds:
                raise ValueError("PLAN_TEST_DELAY_SECONDS is a verification hook and must be 0 in production")
            if self.is_sqlite:  # ADR 0002/0008: one writer at a time is not a production database
                raise ValueError("DATABASE_URL points at SQLite, which production refuses to use. Set a PostgreSQL URL, "
                                 "for example postgresql+psycopg://user:password@host:5432/tripai")
            if self.share_links_enabled and (not self.public_app_url or "localhost" in self.public_app_url or "127.0.0.1" in self.public_app_url):
                raise ValueError("PUBLIC_APP_URL must be set to the web app's real address in production (share links point there); "
                                 "it is only allowed to default to localhost in development")
        return self

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def telemetry_on(self) -> bool:
        return self.telemetry_enabled if self.telemetry_enabled is not None else self.environment != "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def force_demo(self) -> bool:
        return self.demo_mode == "true"

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_maps_server_key) and not self.force_demo

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()