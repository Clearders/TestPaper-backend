from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field

from testpaper_backend.question_images import QUESTION_IMAGE_PATH_PREFIX, normalize_question_image_url
from testpaper_backend.services.images import IMAGE_UPLOAD_DIR

_EMU_PER_INCH = 914400
_PX_PER_INCH = 96
_MAX_IMAGE_WIDTH_EMU = int(5.8 * _EMU_PER_INCH)
_PNG_CONTENT_TYPE = '<Default Extension="png" ContentType="image/png"/>'


@dataclass(frozen=True)
class EmbeddedImage:
    relationship_id: str
    index: int
    content: bytes


@dataclass
class DocxAssetStore:
    images: list[tuple[str, bytes]] = field(default_factory=list)
    image_relationships: list[str] = field(default_factory=list)

    def add_image(self, url: str) -> EmbeddedImage | None:
        image_bytes = _read_uploaded_image(url)
        if image_bytes is None:
            return None

        self.images.append((url, image_bytes))
        image_index = len(self.images)
        relationship_id = f"rIdImage{image_index}"
        self.image_relationships.append(
            f'<Relationship Id="{relationship_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/image{image_index}.png"/>'
        )
        return EmbeddedImage(relationship_id=relationship_id, index=image_index, content=image_bytes)

    def write_media(self, archive: zipfile.ZipFile) -> None:
        for index, (_, image_bytes) in enumerate(self.images, start=1):
            archive.writestr(f"word/media/image{index}.png", image_bytes)

    @property
    def has_images(self) -> bool:
        return bool(self.images)

    @property
    def relationship_xml(self) -> str:
        return "".join(self.image_relationships)


def image_paragraph(image: EmbeddedImage) -> str:
    cx, cy = _image_dimensions_emu(image.content)
    return (
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{image.index}" name="Question Image {image.index}"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<pic:pic><pic:nvPicPr>"
        f'<pic:cNvPr id="{image.index}" name="image{image.index}.png"/>'
        "<pic:cNvPicPr/>"
        "</pic:nvPicPr>"
        "<pic:blipFill>"
        f'<a:blip r:embed="{image.relationship_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch>"
        "</pic:blipFill>"
        "<pic:spPr>"
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</pic:spPr>"
        "</pic:pic>"
        "</a:graphicData></a:graphic>"
        "</wp:inline>"
        "</w:drawing></w:r></w:p>"
    )


def content_types_xml(assets: DocxAssetStore) -> str:
    image_default = _PNG_CONTENT_TYPE if assets.has_images else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{image_default}"
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def package_relationships_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )


def document_relationships_xml(assets: DocxAssetStore) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{assets.relationship_xml}"
        "</Relationships>"
    )


def append_document_relationships(relationships_xml: str, assets: DocxAssetStore) -> str:
    if not assets.image_relationships:
        return relationships_xml
    return relationships_xml.replace("</Relationships>", f"{assets.relationship_xml}</Relationships>", 1)


def ensure_png_content_type(content_types_xml: str, assets: DocxAssetStore) -> str:
    if not assets.has_images or 'Extension="png"' in content_types_xml:
        return content_types_xml
    return content_types_xml.replace("</Types>", f"{_PNG_CONTENT_TYPE}</Types>", 1)


def _read_uploaded_image(url: str) -> bytes | None:
    normalized_url = normalize_question_image_url(url)
    if normalized_url is None:
        return None

    filename = normalized_url[len(QUESTION_IMAGE_PATH_PREFIX) :]
    image_path = IMAGE_UPLOAD_DIR / filename
    if not image_path.is_file():
        return None
    return image_path.read_bytes()


def _image_dimensions_emu(image_bytes: bytes) -> tuple[int, int]:
    image_size = _png_size(image_bytes)
    if image_size is None:
        return _MAX_IMAGE_WIDTH_EMU, int(_MAX_IMAGE_WIDTH_EMU * 0.55)

    width, height = image_size
    cx = int(width / _PX_PER_INCH * _EMU_PER_INCH)
    cy = int(height / _PX_PER_INCH * _EMU_PER_INCH)
    if cx > _MAX_IMAGE_WIDTH_EMU:
        scale = _MAX_IMAGE_WIDTH_EMU / cx
        cx = _MAX_IMAGE_WIDTH_EMU
        cy = int(cy * scale)
    return cx, cy


def _png_size(image_bytes: bytes) -> tuple[int, int] | None:
    if len(image_bytes) < 24 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", image_bytes[16:24])
