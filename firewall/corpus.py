"""Hand-typed truth for saved clips, and a score against it.

Every tuning decision so far -- model size, vocabulary, VAD -- was measured on
synthetic speech pushed through a radio-shaped filter, because there was no
labelled real audio. There is one honest way to fix that: listen to the clips
and type what was actually said.

    firewall --label clips/                 # play, type, next
    firewall --score                        # what the current settings get

The corpus is JSONL, one {audio, text} per line, so it survives any change to
this program and can be edited in any text editor.
"""
import json, os, re, subprocess, sys
from pathlib import Path

from . import core, segments as _segments


def _path(cfg):
    return Path(cfg.get("corpus_path") or "./corpus.jsonl")


def load(cfg):
    p = _path(cfg)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  !  skipping malformed corpus line: {line[:60]}",
                      file=sys.stderr)
    return out


def save(cfg, audio, text):
    """Record one hand-typed truth, replacing any earlier one for that clip."""
    audio = str(audio)
    rows = [c for c in load(cfg) if c.get("audio") != audio]
    rows.append({"audio": audio, "text": text})
    p = _path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def roots(cfg):
    """Directories a clip is allowed to come from. Also the review UI's guard."""
    out = []
    for key in ("incident_dir", "audio_dir"):
        d = cfg.get(key)
        if d and Path(d).exists():
            out.append(Path(d).resolve())
    return out


def _identity(path, ts):
    """(inode, stamp) for one clip: two ways of asking "is this that clip?".

    A call reaches disk twice -- once in audio_dir as the raw archive, once in
    the incident directory as part of the conversation -- and the review list
    should offer it once. Which test answers that depends on when it was
    recorded. Incidents now hard-link the archived bytes, so the inode settles
    it outright. Clips kept before that are real duplicates, separate inodes
    holding identical bytes, and hashing every clip on every poll of the review
    UI would be absurd when the names already answer it: the archive is written
    as {int(ts)}-{tg}.mp3 and the incident stores the same ts, so the same
    second at the same byte count is the same recording. Two distinct calls
    landing on one second with a byte-identical length does not happen.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    return (st.st_dev, st.st_ino), (int(ts), st.st_size) if ts else None


def _resolved(d):
    """A configured directory as a real path, or None if it is not there."""
    if not d:
        return None
    p = Path(d).resolve()
    return p if p.exists() else None


def _spans(keyups):
    """The keyups inside one clip, as playable references into it, or None.

    Only worth building for a clip that holds more than one, and only when the
    log actually recorded where they are: transmissions filed before segments.py
    existed carry no start/end, and a made-up range would send the review UI
    seeking to the wrong voice. cues() does the padding and the clamping, so the
    range you can play here is the range the display plays, off the same file.
    """
    if any(t.get("start") is None or t.get("end") is None for t in keyups):
        return None
    out = _segments.cues([{"start": t["start"], "end": t["end"],
                           "text": t.get("text") or ""} for t in keyups])
    for cue, t in zip(out, keyups):
        cue.pop("audio", None)      # the row's own path is the audio
        cue["ts"] = t.get("ts")
    return out


def catalogue(cfg):
    """Every saved clip, once, with whatever is known about it.

    Incident audio carries its transcript and its call in incident.json, so the
    review UI can show what the machine heard next to the box you type into.
    Loose clips in audio_dir have neither until something transcribes them.

    The incident entry wins any tie, because it is the one that knows the
    department, the call type and what the machine heard. Its archive twin is
    dropped -- but any truth typed against that twin comes with it, since the
    two names were the same audio and the labelling is the expensive part.

    One row is one FILE, never one transmission. A trunked grant that held an
    exchange -- 1787205431, "Dispatch 16, that is a negative." answered by the
    dispatcher's "Clear, thank you." -- is now stored once and pointed at by
    several transmissions with their own start/end (see segments.py). A row per
    transmission listed that clip twice, and worse: a truth is keyed on the
    path, so labelling the second row REPLACED the first one's text with no
    warning, and score() then compared the whole recording against half of it.
    So the keyups are collapsed into one row whose `machine` is the whole
    recording's transcript -- what score() transcribes and what the corpus has
    always been keyed on -- with `spans` carrying the individual keyups so the
    UI can still show and play them for what they are.
    """
    truth = {c["audio"]: c["text"] for c in load(cfg)}
    seen, out = set(), []
    by_inode, by_stamp = {}, {}

    # Resolved, because /api/label stores a truth under the resolved path it
    # got from the request guard. Point FIREWALL_INCIDENT_DIR through a symlink
    # with the raw value here and every label typed in the review UI is written
    # under a path this function never looks up -- it would appear to save and
    # then vanish on the next poll.
    inc_root = _resolved(cfg.get("incident_dir"))
    if inc_root:
        for f in sorted(inc_root.glob("*/incident.json")):
            try:
                inc = json.loads(f.read_text())
            except Exception:
                continue
            by_file = {}
            for t in sorted(inc.get("transmissions", []),
                            key=lambda t: t.get("ts") or 0):
                if t.get("audio"):
                    by_file.setdefault(t["audio"], []).append(t)
            for name, keyups in by_file.items():
                path = str(f.parent / name)
                seen.add(path)
                # The FIRST keyup's timestamp, because that is the clip's own:
                # a transmission is dated clip_ts + its offset into the clip
                # (segments.when), so only the first still names the second the
                # recording started -- which is the second audio_dir wrote into
                # the archive twin's filename, and so the only one the stamp
                # half of _identity can match a legacy duplicate on.
                row = {"path": path, "ts": keyups[0].get("ts"),
                       "machine": " ".join(t["text"] for t in keyups
                                           if t.get("text")).strip(),
                       "truth": truth.get(path),
                       "incident": inc.get("id"), "dept": inc.get("dept"),
                       "type": inc.get("type"),
                       "dispatch": any(t.get("dispatch") for t in keyups)}
                if len(keyups) > 1:
                    row["spans"] = _spans(keyups)
                out.append(row)
                inode, stamp = _identity(path, row["ts"])
                if inode:
                    by_inode.setdefault(inode, row)
                if stamp:
                    by_stamp.setdefault(stamp, row)

    aud = _resolved(cfg.get("audio_dir"))
    if aud:
        for f in sorted(aud.iterdir()):
            if f.suffix.lower() not in (".mp3", ".wav", ".m4a", ".ogg"):
                continue
            path = str(f)
            if path in seen:
                continue
            ts = None
            m = re.match(r"(\d{9,11})-", f.name)
            if m:
                ts = float(m.group(1))
            inode, stamp = _identity(path, ts)
            twin = by_inode.get(inode) or by_stamp.get(stamp)
            if twin:
                if twin["truth"] is None and truth.get(path) is not None:
                    twin["truth"] = truth[path]
                continue
            out.append({"path": path, "ts": ts, "machine": None,
                        "truth": truth.get(path), "incident": None,
                        "dept": None, "type": None, "dispatch": False})

    out.sort(key=lambda c: (c["ts"] or 0), reverse=True)
    return out


def summary(cfg):
    """Counts and mean WER over the clips that have both a truth and a guess."""
    cat = catalogue(cfg)
    rates = [wer(c["truth"], c["machine"]) for c in cat
             if c.get("truth") is not None and c.get("machine")]
    return {"clips": len(cat),
            "labelled": sum(1 for c in cat if c.get("truth") is not None),
            "wer": round(sum(rates) / len(rates), 4) if rates else None}


def _clips(target):
    """One clip, or every clip under a directory (incidents included)."""
    p = Path(target)
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*")
                  if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"))


def label(cfg, target):
    """Play each unlabelled clip and store what you type as its truth."""
    corpus = load(cfg)
    done = {c["audio"] for c in corpus}
    clips = [c for c in _clips(target) if str(c) not in done]
    if not clips:
        print("  nothing new to label here.")
        return 0
    out = _path(cfg)
    print(f"  {len(clips)} unlabelled clip(s). Enter types it, r replays, "
          f"s skips, q quits.\n  Type what was SAID, not what it should have "
          f"been. Blank means no speech.\n")
    for i, clip in enumerate(clips, 1):
        print(f"  [{i}/{len(clips)}] {clip}")
        while True:
            subprocess.run(["afplay", str(clip)], check=False)
            try:
                said = input("      > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  stopped.")
                return 0
            if said == "r":
                continue
            if said == "s":
                said = None
            if said == "q":
                return 0
            break
        if said is None:
            continue
        with out.open("a") as f:
            f.write(json.dumps({"audio": str(clip), "text": said}) + "\n")
        print(f"      saved ({said!r})\n")
    return 0


def _words(s):
    return re.findall(r"[a-z0-9]+", str(s).lower())


def wer(truth, got):
    """Word error rate: edit distance over words, divided by truth length."""
    a, b = _words(truth), _words(got)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1] / len(a)


def score(cfg, verbose=True):
    """Transcribe every labelled clip with the current settings and compare."""
    corpus = load(cfg)
    if not corpus:
        print(f"  no labelled clips yet. Record some audio "
              f"(FIREWALL_AUDIO_DIR), then: firewall --label clips/")
        return 1
    rates, missing = [], 0
    for c in corpus:
        p = Path(c["audio"])
        if not p.exists():
            missing += 1
            continue
        got = core.transcribe(p, cfg)
        r = wer(c["text"], got)
        rates.append(r)
        if verbose and r:
            print(f"  WER {r:4.0%}  said: {c['text']}")
            print(f"            got: {got}")
    if not rates:
        print(f"  every clip in the corpus is missing from disk ({missing}).")
        return 1
    exact = sum(1 for r in rates if r == 0)
    print(f"\n  {len(rates)} clips, model {cfg['whisper_model']}, "
          f"vocab {'custom' if cfg.get('whisper_vocab') else 'built-in'}, "
          f"VAD {'on' if cfg.get('whisper_vad') else 'off'}")
    print(f"  mean WER {sum(rates) / len(rates):.1%}   exact {exact}/{len(rates)}"
          + (f"   ({missing} clips no longer on disk)" if missing else ""))
    return 0
