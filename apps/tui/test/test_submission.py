from apps.tui.src.domain.submission import (
    DraftImage,
    PendingMessage,
    delete_submission_images,
    referenced_images,
    resource_id,
    submission_resources,
)


def test_referenced_images按首次出现排序且去除未引用附件(tmp_path):
    first = DraftImage("image1", tmp_path / "first.png")
    second = DraftImage("image2", tmp_path / "second.png")
    unused = DraftImage("image3", tmp_path / "unused.png")

    assert referenced_images(
        "先 [#image2] 再 [#image1] 和 [#image2]",
        (first, second, unused),
    ) == (second, first)


def test_model_prompt保留原文并生成与图片顺序一致的映射(tmp_path):
    second = DraftImage("image2", tmp_path / "second.png")
    first = DraftImage("image1", tmp_path / "first.png")
    submission = PendingMessage(
        "比较 [#image2] 和 [#image1]",
        (second, first),
    )

    assert submission.model_prompt() == (
        "比较 [#image2] 和 [#image1]\n\n"
        "<attached_images>\n"
        "[#image2] 对应第 1 张附件图片\n"
        "[#image1] 对应第 2 张附件图片\n"
        "</attached_images>"
    )
    assert submission.image_paths == (second.path, first.path)


def test只有图片marker时补充默认模型指令(tmp_path):
    image = DraftImage("image1", tmp_path / "image.png")

    assert PendingMessage(" [#image1] ", (image,)).model_prompt().startswith(
        "请分析所附图片。\n\n<attached_images>"
    )


def test_pending_message自动生成稳定submission_id():
    first = PendingMessage("hello")
    second = PendingMessage("hello")

    assert first.submission_id
    assert second.submission_id
    assert first.submission_id != second.submission_id


def test_submission_resources_use_relative_ids_in_submission_order(tmp_path):
    root = tmp_path / "incoming"
    root.mkdir()
    first = DraftImage("image1", root / "first.png")
    second = DraftImage("image2", root / "second.png")

    resources = submission_resources(
        PendingMessage("[#image2] [#image1]", (second, first)),
        root,
    )

    assert [resource.resource_id for resource in resources] == [
        "second.png",
        "first.png",
    ]
    assert all(resource.media_type is None for resource in resources)


def test_resource_id_rejects_images_outside_controlled_root(tmp_path):
    root = tmp_path / "incoming"
    root.mkdir()
    image = DraftImage("image1", tmp_path / "outside.png")

    try:
        resource_id(image, root)
    except ValueError as error:
        assert "controlled resource root" in str(error)
    else:
        raise AssertionError("outside image must be rejected")


def test_delete_submission_images_only_removes_owned_files(tmp_path):
    owned = tmp_path / "owned.png"
    shared = tmp_path / "shared.png"
    owned.write_bytes(b"owned")
    shared.write_bytes(b"shared")

    delete_submission_images(
        PendingMessage(
            "[#image1] [#image2]",
            (
                DraftImage("image1", owned, owned_temporary_file=True),
                DraftImage("image2", shared),
            ),
        )
    )

    assert owned.exists() is False
    assert shared.exists() is True
