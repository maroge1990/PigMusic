"""SC1: SoundCloud uploads credited only by the uploader's name wait for the
owner's review instead of downloading or being silently dropped."""

import json

import pytest

from server.downloader import Downloader
from server.mix_cache import MixDownloader
from server.providers.extractor import recording
from server.store import Store
from test_artist_identity import client_for, prepared
from test_downloader import queued

UPLOAD = {
    "title": "Test Track",
    "channel": "Test Artist",
    "album": "",
    "duration": 3,
    "provider_track_id": "testartist/test-track",
    "identity_evidence": "uploader_name",
    "quality": "160kbps",
    "extension": ".m4a",
    "url": "https://cf-media.sndcdn.com/stream?Policy=expiring",
}


def engine(store, monkeypatch, found, sources=("soundcloud",)):
    """A worker whose sources answer `found[source]` and whose transfers are
    recorded instead of performed."""
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": list(sources)}
    )
    worker = Downloader(
        store,
        providers={
            source: (lambda query, limit, found=found, source=source: found[source])
            for source in found
            if source != "soundcloud"
        },
    )
    transfers = []
    monkeypatch.setattr(
        worker.runtime,
        "search",
        lambda source, job, context: [dict(c) for c in found.get(source, [])],
    )

    def transfer(job_id, candidate, path):
        transfers.append(candidate)
        return {"identity_evidence": candidate.get("identity_evidence")}

    monkeypatch.setattr(worker, "transfer", transfer)
    monkeypatch.setattr(
        worker,
        "complete",
        lambda job, *a, **k: store.request_state(job["id"], "completed"),
    )
    return worker, transfers


def item(store):
    return store.queue()["items"][0]


def claimed(store):
    job = store.claim_request()
    assert job, store.queue()
    return job


def test_soundcloud_title_only_uploads_are_credited_by_uploader_for_review():
    upload = {"title": "Song", "uploader": "Artist", "duration": 200}
    assert recording(upload, "soundcloud")["identity_evidence"] == "uploader_name"
    assert recording(upload, "soundcloud")["channel"] == "Artist"
    explicit = recording({**upload, "title": "Artist - Song"}, "soundcloud")
    assert explicit["identity_evidence"] == "explicit_credit"


def test_an_uploader_named_like_the_artist_is_held_not_downloaded(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    job = queued(store)
    worker, transfers = engine(store, monkeypatch, {"soundcloud": [UPLOAD]})
    worker.process(job)
    assert transfers == []
    held = item(store)
    assert held["status"] == "failed"
    assert "waiting for review" in held["error"]
    review = held["source_review"]
    assert review["uploader"] == "Test Artist"
    assert review["provider_track_id"] == UPLOAD["provider_track_id"]
    # The public page only; a stream URL expires and must never be stored.
    assert review["url"] == "https://soundcloud.com/testartist/test-track"
    assert "sndcdn" not in json.dumps(held)


@pytest.mark.parametrize("uploader", ["Test Artist & Friends", "Test Artist Fans"])
def test_only_an_exact_uploader_name_is_worth_reviewing(
    tmp_path, monkeypatch, uploader
):
    store = Store(tmp_path)
    job = queued(store)
    worker, transfers = engine(
        store, monkeypatch, {"soundcloud": [{**UPLOAD, "channel": uploader}]}
    )
    worker.process(job)
    assert transfers == []
    assert not item(store).get("source_review")


def test_letter_case_does_not_stop_an_exact_name_match(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = queued(store)
    worker, _ = engine(
        store, monkeypatch, {"soundcloud": [{**UPLOAD, "channel": "TEST ARTIST"}]}
    )
    worker.process(job)
    assert item(store)["source_review"]["uploader"] == "TEST ARTIST"


def test_approving_requeues_and_downloads_that_upload(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = queued(store)
    worker, transfers = engine(store, monkeypatch, {"soundcloud": [UPLOAD]})
    worker.process(job)
    store.review_source(job["id"], approve=True)
    approved = item(store)
    assert approved["status"] == "waiting_for_sources"
    assert approved["source_review"] is None
    worker.process(claimed(store))
    assert [t["provider_track_id"] for t in transfers] == [UPLOAD["provider_track_id"]]
    assert item(store)["status"] == "completed"


def test_rejecting_rules_that_upload_out_for_good(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = queued(store)
    worker, transfers = engine(store, monkeypatch, {"soundcloud": [UPLOAD]})
    worker.process(job)
    store.review_source(job["id"], approve=False)
    rejected = item(store)
    assert rejected["status"] == "failed"
    assert rejected["source_review"] is None
    store.retry_request(job["id"])
    worker.process(claimed(store))
    assert transfers == []
    assert not item(store).get("source_review")


def test_another_source_satisfying_the_request_clears_the_review(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = queued(store)
    official = {
        **UPLOAD,
        "identity_evidence": "structured",
        "provider_track_id": "x",
        "extension": ".mp3",
    }
    worker, transfers = engine(
        store,
        monkeypatch,
        {"soundcloud": [UPLOAD], "zvu4no": [official]},
        sources=("soundcloud", "zvu4no"),
    )
    worker.process(job)
    assert [t["provider_track_id"] for t in transfers] == ["x"]
    assert item(store)["status"] == "completed"
    with pytest.raises(ValueError):
        store.review_source(job["id"], approve=True)


def test_a_retry_that_no_longer_finds_the_upload_leaves_no_stale_review(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    job = queued(store)
    found = {"soundcloud": [UPLOAD]}
    worker, _ = engine(store, monkeypatch, found)
    worker.process(job)
    found["soundcloud"] = []
    store.retry_request(job["id"])
    worker.process(claimed(store))
    assert not item(store).get("source_review")


def test_a_cancelled_or_running_request_cannot_be_decided(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = queued(store)
    worker, _ = engine(store, monkeypatch, {"soundcloud": [UPLOAD]})
    worker.process(job)
    store.retry_request(job["id"])
    running = claimed(store)
    store.request_state(running["id"], "searching", source_review=UPLOAD)
    with pytest.raises(ValueError):
        store.review_source(job["id"], approve=True)
    store.cancel_request(job["id"])
    with pytest.raises(ValueError):
        store.review_source(job["id"], approve=False)
    with pytest.raises(LookupError):
        store.review_source("missing", approve=True)


def test_mix_preparation_never_asks_the_owner():
    assert MixDownloader.hold(object(), {}, "soundcloud", UPLOAD) is None


def test_only_the_owner_can_decide_a_held_upload(tmp_path, monkeypatch):
    app, owner_token, member_token, _ = prepared(tmp_path, monkeypatch)
    store = app.state.store
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO queue(id,status,payload) VALUES (?,?,?)",
            (
                "held",
                "failed",
                json.dumps({"title": "Song", "source_review": UPLOAD}),
            ),
        )
    path = "/api/v1/admin/queue/held/source-review/"
    with client_for(app, member_token) as member:
        assert member.post(path + "approve").status_code in {401, 403, 404}
    with client_for(app, owner_token) as owner:
        assert owner.post(path + "maybe").status_code == 422
        assert (
            owner.post("/api/v1/admin/queue/nope/source-review/approve").status_code
            == 404
        )
        decided = owner.post(path + "reject")
        assert decided.status_code == 200
        entry = next(i for i in decided.json()["items"] if i["id"] == "held")
        assert entry["source_review_rejected"] == [UPLOAD["provider_track_id"]]
        assert owner.post(path + "reject").status_code == 409


# Review of 5f01c36: the held upload must survive stronger candidates failing,
# and re-extraction must never download weaker credit without approval.

STRONG = {
    **UPLOAD,
    "identity_evidence": "structured",
    "provider_track_id": "label/test-track",
}


def test_a_failing_stronger_candidate_still_leaves_the_upload_for_review(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    job = queued(store)
    worker, _ = engine(store, monkeypatch, {"soundcloud": [STRONG, UPLOAD]})
    tried = []

    def fail(job_id, candidate, path):
        tried.append(candidate["provider_track_id"])
        raise ValueError("candidate failed validation")

    monkeypatch.setattr(worker, "transfer", fail)
    worker.process(job)
    held = item(store)
    # The stronger one was tried first; the upload was never transferred.
    assert set(tried) == {STRONG["provider_track_id"]}
    assert held["status"] == "failed"
    assert held["source_review"]["provider_track_id"] == UPLOAD["provider_track_id"]


def test_quality_filtering_the_stronger_candidate_keeps_the_upload_for_review(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    job = {**queued(store), "quality_profile": "mp3_128"}
    job["quality_policy"] = {
        "preferred": "mp3_128",
        "fallback": "none",
        "fallback_minutes": 10,
        "recheck_hours": 24,
    }
    stronger = {**STRONG, "quality": "320kbps"}  # Not an exact 128 kbps file.
    worker, transfers = engine(
        store, monkeypatch, {"soundcloud": [stronger, {**UPLOAD, "quality": "128"}]}
    )
    worker.process(job)
    assert transfers == []
    assert item(store)["source_review"]["provider_track_id"] == (
        UPLOAD["provider_track_id"]
    )


# What re-extraction found: the same song, credited only by its uploader.
FRESH = {"title": "Test Track", "channel": "Test Artist", "duration": 3.4}


def test_weaker_credit_found_at_transfer_goes_back_to_review(tmp_path, monkeypatch):
    from server.providers.contracts import ProviderError

    store = Store(tmp_path)
    job = queued(store)
    worker, _ = engine(store, monkeypatch, {"soundcloud": [STRONG]})

    def weakened(job_id, candidate, path):
        assert not candidate.get("uploader_approved")
        raise ProviderError("review_required", review=FRESH)

    monkeypatch.setattr(worker, "transfer", weakened)
    worker.process(job)
    held = item(store)
    assert held["source_review"]["provider_track_id"] == STRONG["provider_track_id"]
    # Approving it lets the same upload through on the next attempt.
    store.review_source(job["id"], approve=True)
    approved = []
    monkeypatch.setattr(
        worker,
        "transfer",
        lambda job_id, candidate, path: approved.append(candidate) or {},
    )
    worker.process(claimed(store))
    # The approval is bound to the uploader the owner was shown.
    assert approved[0]["uploader_approved"] == FRESH["channel"]
    assert item(store)["status"] == "completed"


def extractor_transfer(tmp_path, monkeypatch, original, evidence):
    from pathlib import Path

    from server.providers.contracts import Context
    from server.providers.extractor import Extractor

    extractor = Extractor("soundcloud")
    requests = []
    fresh = {
        **original,
        **recording(
            {"title": "Test Track", "uploader": "Test Artist", "duration": 3},
            "soundcloud",
        ),
        "identity_evidence": evidence,
        "original_codec": "aac",
        "original_container": "m4a",
        "declared_bitrate": 160,
    }

    def invoke(request, context):
        requests.append(request)
        (Path(request["directory"]) / "audio.m4a").write_bytes(b"fixture")
        return {"candidate": fresh, "filename": "audio.m4a", "remuxed": False}

    monkeypatch.setattr(extractor, "invoke", invoke)
    target = tmp_path / "result.m4a"
    result = extractor.transfer(original, target, Context("native_standard"))
    return result, requests, target


def test_the_parent_rejects_newly_weak_credit_without_approval(tmp_path, monkeypatch):
    from server.providers.contracts import ProviderError

    original = {**STRONG, "format_id": "download"}
    with pytest.raises(ProviderError) as caught:
        extractor_transfer(tmp_path, monkeypatch, original, "uploader_name")
    assert caught.value.code == "review_required"
    assert not (tmp_path / "result.m4a").exists()


def test_an_approved_uploader_only_transfer_still_works(tmp_path, monkeypatch):
    original = {**UPLOAD, "format_id": "download", "uploader_approved": "Test Artist"}
    result, requests, target = extractor_transfer(
        tmp_path, monkeypatch, original, "uploader_name"
    )
    assert requests[0]["uploader_approved"] == "Test Artist"
    assert result["identity_evidence"] == "uploader_name"
    assert target.is_file()


def test_the_worker_refuses_uploader_only_credit_before_downloading(
    tmp_path, monkeypatch
):
    from yt_dlp import YoutubeDL

    from server.providers.contracts import ProviderError
    from server.providers.extractor_worker import execute

    info = {
        "id": "1",
        "title": "Test Track",
        "uploader": "Test Artist",
        "duration": 3,
        "webpage_url": "https://soundcloud.com/testartist/test-track",
        "formats": [
            {
                "format_id": "download",
                "format_note": "Original",
                "ext": "m4a",
                "acodec": "mp4a.40.2",
                "vcodec": "none",
                "abr": 160,
                "protocol": "https",
                "url": "https://cf-media.sndcdn.com/audio.m4a",
            }
        ],
    }
    downloads = []
    monkeypatch.setattr(YoutubeDL, "extract_info", lambda *a, **k: dict(info))
    monkeypatch.setattr(YoutubeDL, "process_info", lambda *a, **k: downloads.append(1))
    request = {
        "operation": "transfer",
        "source": "soundcloud",
        "profile": "native_standard",
        "provider_track_id": "testartist/test-track",
        "format_id": "download",
        "directory": str(tmp_path),
    }
    with pytest.raises(ProviderError) as caught:
        execute(request)
    assert caught.value.code == "review_required"
    assert downloads == []


def test_mix_preparation_never_downloads_an_unapproved_upload(tmp_path, monkeypatch):
    from server.providers.contracts import ProviderError
    from test_mixes import protected

    app, _ = protected(tmp_path, monkeypatch)
    store = app.state.store
    store.save_download_settings({"enabled": True, "sources": ["soundcloud"]})
    downloader = MixDownloader(store.mix_cache, app.state.source_runtime, lambda: False)
    found = [STRONG, UPLOAD]
    monkeypatch.setattr(
        downloader.runtime, "search", lambda *a: [dict(c) for c in found]
    )
    transfers = []

    def transfer(job_id, candidate, path):
        transfers.append(candidate)
        assert not candidate.get("uploader_approved")
        raise ProviderError("review_required")  # Re-extraction weakened it.

    monkeypatch.setattr(downloader, "transfer", transfer)
    song = {"title": "Test Track", "artist": "Test Artist", "album": "", "duration": 3}
    assert not downloader.fetch(song)
    assert [t["provider_track_id"] for t in transfers] == [STRONG["provider_track_id"]]
    assert store.queue()["items"] == []


# RV23 review: a refusal at transfer is judged on what re-extraction found.


def through_the_worker(store, monkeypatch, found, fresh_upload):
    """The real worker subprocess code (yt-dlp stubbed) behind the real
    extractor, so the refusal and its metadata travel the production path."""
    from yt_dlp import YoutubeDL

    from server.providers.contracts import Context
    from server.providers.extractor import Extractor
    from server.providers.extractor_worker import execute

    worker, _ = engine(store, monkeypatch, found)
    info = {
        "duration": 3,
        "webpage_url": "https://soundcloud.com/label/test-track",
        "formats": [
            {
                "format_id": "download",
                "format_note": "Original",
                "ext": "m4a",
                "acodec": "mp4a.40.2",
                "vcodec": "none",
                "abr": 160,
                "protocol": "https",
                "url": "https://cf-media.sndcdn.com/audio.m4a?Policy=expiring",
            }
        ],
        **fresh_upload,
    }
    downloads = []
    monkeypatch.setattr(YoutubeDL, "extract_info", lambda *a, **k: dict(info))
    monkeypatch.setattr(YoutubeDL, "process_info", lambda *a, **k: downloads.append(1))
    extractor = Extractor("soundcloud")
    monkeypatch.setattr(
        extractor,
        "invoke",
        lambda request, context: execute(
            {**request, "source": "soundcloud", "profile": "native_standard"}
        ),
    )
    monkeypatch.setattr(
        worker,
        "transfer",
        lambda job_id, candidate, path: extractor.transfer(
            candidate, path, Context("native_standard")
        ),
    )
    return worker, downloads


def test_a_different_uploader_found_at_transfer_is_never_offered(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = queued(store)
    worker, downloads = through_the_worker(
        store,
        monkeypatch,
        {"soundcloud": [{**STRONG, "format_id": "download"}]},
        {"title": "Test Track", "uploader": "Unrelated Fan Account"},
    )
    worker.process(job)
    assert downloads == []
    assert not item(store).get("source_review")


def test_a_qualifying_fresh_uploader_is_reviewed_as_found(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = queued(store)
    worker, downloads = through_the_worker(
        store,
        monkeypatch,
        {"soundcloud": [{**STRONG, "format_id": "download"}]},
        {"title": "TEST TRACK", "uploader": "TEST ARTIST", "duration": 3.4},
    )
    worker.process(job)
    assert downloads == []
    review = item(store)["source_review"]
    assert review["title"] == "TEST TRACK"
    assert review["uploader"] == "TEST ARTIST"
    assert review["duration"] == 3.4
    assert review["url"] == "https://soundcloud.com/" + STRONG["provider_track_id"]
    assert "sndcdn" not in json.dumps(item(store)) and "Policy" not in json.dumps(
        item(store)
    )


@pytest.mark.parametrize(
    "fresh",
    [
        {"title": "Another Song", "uploader": "Test Artist"},
        {"title": "Test Track", "uploader": "Test Artist", "duration": 240},
        {"title": "Test Track (Live)", "uploader": "Test Artist"},
    ],
)
def test_fresh_metadata_that_no_longer_matches_is_rejected(
    tmp_path, monkeypatch, fresh
):
    store = Store(tmp_path)
    job = queued(store)
    worker, downloads = through_the_worker(
        store, monkeypatch, {"soundcloud": [{**STRONG, "format_id": "download"}]}, fresh
    )
    worker.process(job)
    assert downloads == []
    assert not item(store).get("source_review")


def test_an_approval_is_for_the_uploader_the_owner_saw(tmp_path, monkeypatch):
    from pathlib import Path

    from server.providers.contracts import Context, ProviderError
    from server.providers.extractor import Extractor

    # Approved "Test Artist", but re-extraction now finds someone else.
    original = {**UPLOAD, "format_id": "download", "uploader_approved": "Test Artist"}
    fresh = {
        **original,
        "channel": "Unrelated Fan Account",
        "identity_evidence": "uploader_name",
        "original_codec": "aac",
        "original_container": "m4a",
        "declared_bitrate": 160,
    }
    extractor = Extractor("soundcloud")

    def invoke(request, context):
        (Path(request["directory"]) / "audio.m4a").write_bytes(b"fixture")
        return {"candidate": fresh, "filename": "audio.m4a", "remuxed": False}

    monkeypatch.setattr(extractor, "invoke", invoke)
    with pytest.raises(ProviderError) as caught:
        extractor.transfer(original, tmp_path / "out.m4a", Context("native_standard"))
    assert caught.value.code == "review_required"
    assert caught.value.review["channel"] == "Unrelated Fan Account"
    assert not (tmp_path / "out.m4a").exists()


def test_review_metadata_carries_nothing_but_safe_fields():
    from server.providers.contracts import ProviderError, review_metadata

    fresh = {
        "title": "Song",
        "channel": "Artist",
        "duration": 200,
        "url": "https://cf-media.sndcdn.com/x?Policy=secret",
        "format_id": "download",
        "cookies": "secret",
    }
    assert review_metadata(fresh) == {
        "title": "Song",
        "channel": "Artist",
        "duration": 200.0,
    }
    assert ProviderError("no_match", review=fresh).review is None
    assert ProviderError("review_required", review={"title": "x"}).review is None
