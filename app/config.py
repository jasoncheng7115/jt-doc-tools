from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JTDT_", env_file=".env", extra="ignore")

    app_name: str = "Jason Tools 文件工具箱"
    host: str = "127.0.0.1"
    port: int = 8765
    debug: bool = False

    project_root: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"

    job_ttl_seconds: int = 60 * 60 * 6
    cleanup_interval_seconds: int = 60 * 30
    # Uploaded source files & intermediate previews live under temp_dir.
    # Anything untouched for this many seconds is swept by the cleanup
    # task. Keep shorter than job_ttl so users don't get broken previews.
    temp_ttl_seconds: int = 60 * 60 * 2  # 2 hours

    default_paper_mm: tuple[float, float] = (210.0, 297.0)

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def assets_files_dir(self) -> Path:
        return self.data_dir / "assets" / "files"

    @property
    def assets_meta_path(self) -> Path:
        return self.data_dir / "assets" / "assets.json"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "temp"

    @property
    def fonts_dir(self) -> Path:
        return self.data_dir / "fonts"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.assets_dir, self.assets_files_dir,
                  self.jobs_dir, self.temp_dir, self.fonts_dir):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
