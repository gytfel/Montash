from montash.download import _explain, collect_videos, is_url


def test_is_url():
    assert is_url("https://youtu.be/abc") and is_url("  http://vk.com/video1_2 ")
    assert not is_url(r"C:\Videos\film.mp4") and not is_url("https://a b")


def test_collect_videos_expands_folders_in_natural_order(tmp_path):
    folder = tmp_path / "film"
    folder.mkdir()
    for name in ("part10.mkv", "part2.mkv", "part1.mkv", "notes.txt"):
        (folder / name).write_bytes(b"x")
    (folder / "sub").mkdir()
    single = tmp_path / "extra.MP4"
    single.write_bytes(b"x")

    found = collect_videos([str(folder), str(single), str(folder / "part1.mkv"), str(tmp_path / "missing.mp4")])
    assert [p.split("/")[-1].split("\\")[-1] for p in found] == ["part1.mkv", "part2.mkv", "part10.mkv", "extra.MP4"]


def test_explain_common_errors():
    assert "не поддерживается" in _explain("ERROR: Unsupported URL: https://example.com")
    assert "DRM" in _explain("ERROR: [x] 1: This video is DRM protected")
    assert "404" in _explain("ERROR: Unable to download webpage: HTTP Error 404: Not Found")
    assert "входа" in _explain("ERROR: [youtube] x: Sign in to confirm your age")
