"""A profile that records what was called and writes dummy results."""

from __future__ import annotations

from pathlib import Path

from rigconductor.profiles.base import Profile, SessionContext


class FakeProfile(Profile):
    name = "fake"
    organism = "fake"
    title = "fake"
    n_units = 2
    unit_noun = "arena"

    def __init__(self, fail_on: str | None = None, poll_ok: bool = True):
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.poll_ok = poll_ok

    def _hit(self, name):
        self.calls.append(name)
        if name == self.fail_on:
            raise RuntimeError(f"boom in {name}")

    def validate_metadata(self, meta, cfg):
        return [] if meta.get("ok") else ["metadata says no"]

    def cut(self, ctx: SessionContext):
        self._hit("cut")
        ctx.clips_dir.mkdir(exist_ok=True)
        clips = [ctx.clips_dir / f"a{i}.mp4" for i in range(self.n_units)]
        for c in clips:
            c.write_bytes(b"x")
        return clips

    def register(self, ctx):
        self._hit("register")
        ctx.state["trials"] = {"0": "T0", "1": "T1"}

    def upload(self, ctx):
        self._hit("upload")

    def submit(self, ctx, model):
        self._hit("submit")
        ctx.state["model_run"] = model.run
        return "4242"

    def poll(self, ctx, interval_s):
        self._hit("poll")
        return self.poll_ok

    def fetch(self, ctx):
        self._hit("fetch")
        ctx.results_dir.mkdir(exist_ok=True)
        p = ctx.results_dir / "pose.h5"
        p.write_bytes(b"h5")
        return [p]

    def score(self, ctx, model):
        self._hit("score")
        (ctx.results_dir / "events.csv").write_text("trial,beh\nT0,lunge\n", encoding="utf-8")

    def export_files(self, ctx, blind, clips):
        self._hit("export")
        files = {p.name: p for p in ctx.results_dir.iterdir() if p.is_file()}
        if clips != "none":
            for c in ctx.clips_dir.glob("*.mp4"):
                files[f"clips/{c.name}"] = c
        return files

    def preview_image(self, ctx) -> Path | None:
        return None


FakeProfile.inverts_for = lambda self, ctx, model: True
