"""Тест отдачи артефактов юзеру файлом: ArtifactDelivery.send_attachments."""

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import InputMediaDocument, InputMediaPhoto
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.telegram.artifact_delivery import ArtifactDelivery


def _delivery(read_bytes: AsyncMock) -> tuple[ArtifactDelivery, MagicMock]:
    """ArtifactDelivery с замоканными artifacts/bot; возвращает (delivery, tg-mock)."""
    artifacts = MagicMock()
    artifacts.read_bytes = read_bytes
    tg = MagicMock()
    tg.send_document = AsyncMock()
    tg.send_photo = AsyncMock()
    tg.send_media_group = AsyncMock()
    return ArtifactDelivery(bot=tg, artifacts=artifacts), tg


def _ref(
    *,
    type_: str = "document",
    artifact_id: str = "a1",
    filename: str = "report.csv",
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        type=type_,
        artifact_user_name=filename,
        storage_key=f"u/{artifact_id}/data",
    )


@pytest.mark.asyncio
async def test_send_attachments_document_with_user_name() -> None:
    """Документ качается по storage_key и шлётся send_document с artifact_user_name."""
    read = AsyncMock(return_value=b"data")
    delivery, tg = _delivery(read)

    await delivery.send_attachments(chat_id=1, request_id="r1", attachments=[_ref()])

    read.assert_awaited_once_with("u/a1/data")
    tg.send_document.assert_awaited_once()
    assert tg.send_document.call_args.kwargs["document"].filename == "report.csv"
    tg.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_attachments_image_uses_send_photo() -> None:
    """type=image → send_photo вместо send_document."""
    delivery, tg = _delivery(AsyncMock(return_value=b"img"))

    await delivery.send_attachments(
        chat_id=1,
        request_id="r1",
        attachments=[_ref(type_="image", filename="cat.png")],
    )

    tg.send_photo.assert_awaited_once()
    tg.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_attachments_download_failure_skips_one() -> None:
    """Сбой read_bytes одного артефакта не рушит остальные."""

    async def _read(key: str) -> bytes:
        if key == "u/bad/data":
            raise RuntimeError("boom")
        return b"ok"

    delivery, tg = _delivery(AsyncMock(side_effect=_read))

    await delivery.send_attachments(
        chat_id=1,
        request_id="r1",
        attachments=[
            _ref(artifact_id="bad"),
            _ref(artifact_id="good", filename="g.csv"),
        ],
    )

    tg.send_document.assert_awaited_once()
    assert tg.send_document.call_args.kwargs["document"].filename == "g.csv"


@pytest.mark.asyncio
async def test_two_images_sent_as_single_media_group() -> None:
    """2 фото → один send_media_group с двумя InputMediaPhoto, без send_photo."""
    delivery, tg = _delivery(AsyncMock(return_value=b"img"))

    await delivery.send_attachments(
        chat_id=1,
        request_id="r1",
        attachments=[
            _ref(type_="image", artifact_id="a", filename="1.png"),
            _ref(type_="image", artifact_id="b", filename="2.png"),
        ],
    )

    tg.send_media_group.assert_awaited_once()
    media = tg.send_media_group.call_args.kwargs["media"]
    assert len(media) == 2
    assert all(isinstance(m, InputMediaPhoto) for m in media)
    tg.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_types_split_into_two_albums_photos_first() -> None:
    """Фото и документы — раздельные альбомы; сначала фото, затем документы."""
    delivery, tg = _delivery(AsyncMock(return_value=b"data"))

    await delivery.send_attachments(
        chat_id=1,
        request_id="r1",
        attachments=[
            _ref(type_="image", artifact_id="p1", filename="1.png"),
            _ref(type_="image", artifact_id="p2", filename="2.png"),
            _ref(type_="image", artifact_id="p3", filename="3.png"),
            _ref(type_="document", artifact_id="d1", filename="a.csv"),
            _ref(type_="document", artifact_id="d2", filename="b.csv"),
        ],
    )

    assert tg.send_media_group.await_count == 2
    first_media = tg.send_media_group.call_args_list[0].kwargs["media"]
    second_media = tg.send_media_group.call_args_list[1].kwargs["media"]
    assert len(first_media) == 3
    assert all(isinstance(m, InputMediaPhoto) for m in first_media)
    assert len(second_media) == 2
    assert all(isinstance(m, InputMediaDocument) for m in second_media)


@pytest.mark.asyncio
async def test_eleven_images_chunk_ten_plus_single() -> None:
    """11 фото → альбом из 10 + остаток-в-1 обычным send_photo."""
    delivery, tg = _delivery(AsyncMock(return_value=b"img"))

    attachments = [
        _ref(type_="image", artifact_id=f"p{i}", filename=f"{i}.png") for i in range(11)
    ]
    await delivery.send_attachments(chat_id=1, request_id="r1", attachments=attachments)

    tg.send_media_group.assert_awaited_once()
    assert len(tg.send_media_group.call_args.kwargs["media"]) == 10
    tg.send_photo.assert_awaited_once()
