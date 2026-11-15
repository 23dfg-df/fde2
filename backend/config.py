import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    data: Path
    model_url: str = "http://127.0.0.1:18401/v1"
    model: str = "tencent/HunyuanOCR"
    model_revision: str = "47644ecc4fc854efa4f505155158831f36773ee4"
    physical_gpu: int = 1
    request_timeout: float = 180

    @classmethod
    def load(cls):
        root = Path(__file__).resolve().parents[1]
        return cls(
            root=root,
            data=Path(os.getenv("OCR_DATA", str(root / "data"))),
            model_url=os.getenv("OCR_MODEL_URL", "http://127.0.0.1:18401/v1").rstrip("/"),
            model=os.getenv("OCR_MODEL_NAME", "tencent/HunyuanOCR"),
            model_revision=os.getenv("OCR_MODEL_REVISION", cls.model_revision),
            physical_gpu=int(os.getenv("OCR_PHYSICAL_GPU", "1")),
            request_timeout=float(os.getenv("OCR_REQUEST_TIMEOUT", "180")),
        )
