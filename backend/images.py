import io
import math
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_PAGES = 6
MAX_SIDE = 1600
MAX_PIXELS = 24_000_000


class UploadError(ValueError):
    pass


def normalize_image(raw):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise UploadError("仅接受PNG、JPEG、WebP或PDF。")
                if image.width * image.height > MAX_PIXELS:
                    raise UploadError("图片像素过大，请缩小后上传。")
                if getattr(image, "n_frames", 1) != 1:
                    raise UploadError("暂不接受动画或多帧图片。")
                image.load()
                image = ImageOps.exif_transpose(image)
                if image.mode == "RGBA" or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    canvas = Image.new("RGBA", rgba.size, "white")
                    canvas.alpha_composite(rgba)
                    image = canvas.convert("RGB")
                else:
                    image = image.convert("RGB")
                image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
                return image.copy()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise UploadError("图片损坏或像素超限，无法读取。") from error


def decode_upload(filename, raw):
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise UploadError("文件为空或超过12 MiB。")
    suffix = Path(filename or "upload").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}:
        raise UploadError("仅接受PNG、JPEG、WebP或PDF扩展名。")
    if suffix != ".pdf":
        return [normalize_image(raw)]
    if not raw.lstrip().startswith(b"%PDF-"):
        raise UploadError("文件内容不是PDF。")
    import fitz
    try:
        with fitz.open(stream=raw, filetype="pdf") as document:
            if document.needs_pass:
                raise UploadError("暂不支持加密PDF。")
            if not 1 <= len(document) <= MAX_PAGES:
                raise UploadError("PDF需为1-6页，请先拆分。")
            images = []
            for page in document:
                width, height = page.rect.width, page.rect.height
                if not all(math.isfinite(x) and 0 < x <= 20000 for x in (width, height)):
                    raise UploadError("PDF页面尺寸无效或过大。")
                zoom = min(2, MAX_SIDE / max(width, height))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
                images.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
            return images
    except UploadError:
        raise
    except Exception as error:
        raise UploadError("PDF损坏或无法渲染。") from error
