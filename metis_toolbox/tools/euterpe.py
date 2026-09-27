"""
euterpe.py — Song Ledger
=========================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Remember what music has played, and answer two questions about it:
             "have we heard this recently?" and "what does the listener
             actually like?". One row per song, plus the views over it. It
             does not choose songs, play them, or talk to mpv — Awen chooses,
             Morpheus plays.

One row      It was an append-only log until 2026-09-17, and it stopped being
per song:    one on the same day it stopped being the DJ's. A list of songs
             somebody keeps has no use for a second copy of a song: the anchor
             de-duplicates before reading, the no-repeat window de-duplicates
             before printing, and the only thing a duplicate reliably does is
             push a real song out of a 20-row window and take up a line in the
             editor.

             So the two write paths both hold the rule, differently, and the
             difference is deliberate:

               record()      MOVES. Playing a song already on the list drops
                             the old row and appends a new one, so the replay
                             refreshes where that song sits in the anchor —
                             playing it again IS a current signal, and a rule
                             that kept the first timestamp would throw that
                             away to preserve a history nobody reads.
               replace_all() REFUSES, naming both rows. It is the function that
                             promises not to rewrite what was typed, and
                             silently dropping a line the operator can see on
                             screen is how a file stops matching its editor.

             Identity is the normalized key alone, never also the video id.
             Collapsing one song's many uploads is the whole reason normalize()
             exists (see "Dedupe"), so a second test on the id could only split
             a row the key had already joined.

Whose log:   Since 2026-09-17 this holds the songs the LISTENER chose, and only
             those. Awen used to file its own picks here too and stopped: with
             an editor on the file (Vox Array → TUNES) it is a list somebody
             curates, and a job that writes to the list it reads from is a
             feedback loop — one of which already happened, see "Dedupe" and
             awen.py. Nothing in THIS module enforces that, deliberately.
             record() still takes source="dj" and always will; who calls it is
             Awen's business, and a module that policed its callers would be
             guessing at their job instead of doing its own.

Why not      Morpheus's Job line says "no play history", and that is not an
Morpheus:    oversight to correct. Morpheus owns an mpv process and a transport;
             a play log is a different job with a different lifetime (it
             outlives the process, it outlives the app) and a different failure
             mode (a corrupt file must not stop the music). Bolting it on would
             give one module two reasons to change. So this observes instead:
             Awen reads Morpheus's cached snapshot on its own Kairos tick and
             calls record() here. Morpheus never learns this module exists.

Contract:    Local-only ledger / persistence flavor (§2) — the plutus.py
             precedent. No fetch(), no TOOL_DEFINITION, no tkinter, no network,
             no threads. Plain functions over one JSON file.

               record(...)              — append one confirmed play, or decline
                                          it. Returns the stored entry or None.
               entries()                — the whole log, oldest first.
               recent(limit=None)       — the last N entries.
               recent_titles(limit=None)— the no-repeat window, normalized.
               was_recently_played(...) — the guard's question, by id or title.
               taste_anchor(limit=None) — the listener's OWN recent picks.
               normalize(title, channel) — the dedupe key.
               video_id(url)            — the YouTube id, when there is one.
               settings()               — the config, with defaults applied.

             And the editor's three, which no other caller uses:

               raw_text()               — the file's literal text.
               stamp()                  — its mtime; the editor's staleness token.
               replace_all(text)        — validate and write a WHOLE ledger.
                                          None on success, an error slug on refusal.
               base_snapshot()          — the base copy, or None.
               take_base()              — make the ledger on disk the base copy.

             Out of LLM scope, deliberately (§2), and not for the usual
             "it mutates real money" reason. The no-repeat rule is a guard the
             model must not be able to argue with: give it a write handle and a
             model that wants to replay a song can record its way around the
             check, and give it a read handle and it pays for a tool round trip
             to learn something Awen already puts in the turn's own message.

Derive,      Nothing computed is stored (§9). The log is the truth; the
don't store: no-repeat window and the taste anchor are VIEWS over it, both
             windowed by config at read time. So changing a window size is a
             JSON edit that takes effect on the next read, with no migration and
             no stale derived field to disagree with the log.

Never        record() swallows and logs instead of raising, which deviates from
raises:      the persistence norm plutus.py follows (_save_events raises so the
             caller can surface it). The difference is who is watching. Plutus
             writes from a Save button with a human in front of it. This writes
             from a Kairos worker on a job nobody asked for and nobody is
             looking at, so there is no one to surface it TO — and a disk hiccup
             must not take down the tick or stop the music. It goes to the log,
             where Emanon surfaces it. See CONVENTIONS §12.

No delete:   There is no clear(), erase() or undo, and that is deliberate
             (the herodotus.py precedent). Reset Session clears the chat and
             leaves this alone on purpose: a no-repeat list that can be wiped
             mid-session is not a no-repeat list. Tests redirect _LEDGER_PATH at
             a temp file rather than asking for a reset hook.

             replace_all() is not a hole in that, and the difference is worth
             stating so the next reader doesn't "fix" one of the two. What the
             rule forbids is a PROGRAMMATIC erase — a hook that Reset Session,
             a panel button or the model could call to wipe history as a side
             effect of doing something else. replace_all() is the operator's
             own hand: reached only from the TUNES editor, behind a deliberate
             SAVE, on a file they could always have opened in Notepad and once
             had to (the 2026-09-16 repair, when eight of the DJ's own picks
             were logged as the listener's taste and the only recourse was a
             script and a backup file). There is still no clear(), and this
             module is still out of _TOOL_MODULES, so the MODEL still has no
             write handle — which was always the point. See CONVENTIONS §12.

Is it a       is_a_song() is the ONE place that decides, and both callers ask
song?         it: this module deciding what counts as a prior, and Awen's guard
              deciding what the DJ may play. They must not be able to disagree —
              a thing the DJ is forbidden to play but the ledger happily stores
              becomes a prior the DJ can never match, and the reverse lets it
              play what the ledger then refuses to remember.

              Three tests, all config (§4):
                - too short: below min_song_seconds. A 26 s room-echo demo is
                  not a track, and one steered a real evening.
                - too long, or NO DURATION: above max_song_seconds, or a live
                  stream, which yt-dlp reports as None and which never ends —
                  so the trigger that fires the next pick never fires again.
                - not music by title: see _DEFAULT_NOT_MUSIC. Duration alone
                  does not separate a song from a 282 s gear manual, and an
                  evening measured 2026-09-16 produced a user manual, a hardware
                  review, a documentary and a reaction video, every one of which
                  passed a cap-only guard.

              The title half is a DENYLIST and is therefore config rather than
              code: every denylist is eventually wrong about something, and when
              it refuses a real track the fix should be a JSON edit, not a
              release. An explicitly empty list turns it off.

Dedupe:      Two keys, because neither works alone.
               - the YouTube video id, when the caller knows it. Exact, but
                 only DJ picks and anything routed through play_music carry a
                 URL; a play started from the Morpheus panel reaches Awen as a
                 title off mpv's snapshot and nothing else.
               - the NORMALIZED title, which always works. YouTube ships one
                 song under many uploads: a single search for "Fleetwood Mac
                 Dreams" returned it five times, as "(Official Music Video)
                 [4K]", "(2004 Remaster)", "(Official Audio)", "(Lyrics)" and
                 "(Official Vinyl Video)" (measured 2026-09-16). Without
                 normalization the DJ can play the same song five times and
                 every one of them passes an exact-title check.

             The channel feeds the second key, because one of those five was
             titled just "Dreams (2004 Remaster)" with no artist in it at all.
             normalize() borrows the artist from the channel when the title has
             none of its own — never otherwise, so it can sharpen a vague key
             and cannot spoil a good one.

             ONE JUDGMENT, and every de-duplicating caller asks it. same_song()
             takes the order and the case out of a key; the guard, record()'s
             move-on-replay, replace_all()'s duplicate check, recent_titles()
             and taste_anchor() all go through it. The last two did not until
             2026-09-19 — they de-duped with `key not in seen`, which compares
             raw strings, so the list the MODEL reads could name one song twice
             while the guard refused it. A second opinion about what one song is
             is worse than no opinion, because only one of them is enforced.

             What defeats a key, all four measured on real uploads rather than
             imagined: a different CASE (the live ledger held one, typed through
             TUNES, which does not re-derive what you type); the order across
             the dash; a "feat." tail in whichever bracket the uploader reached
             for; and a "- Remastered 2011" / "- Live at X" qualifier, which is
             the house style of the "- Topic" channels the DJ now prefers —
             that one is doubly damaging, because the dash also stops the
             channel-derived artist ever being applied.

Persistence: euterpe_ledger.json at the app root, gitignored (§9 — generated,
             personal). {"plays": [...]}, oldest first, bounded to _MAX_ENTRIES.
             Atomic write (tempfile in the same dir + os.replace), so a crash
             mid-write cannot truncate it. A missing or corrupt file reads as an
             empty history, logged once, never raised.

             A hand edit through replace_all() copies the old file to
             euterpe_ledger.json.bak first — ONE rolling backup, not a pile.
             The log has no undo, and a valid edit that deletes sixty rows is
             indistinguishable from an intended one; if the backup cannot be
             written the save is refused rather than taken without a net.

Base copy:   euterpe_ledger.json.base is a SECOND kind of safety net and not a
             spare of the first. The .bak rolls: it protects the one save you
             just made and is gone after the next. The .base is taken
             deliberately by the operator and nothing in this module ever
             overwrites it, so it survives any number of saves — it is the
             answer to "put it back the way it was when I had it right", which
             a rolling backup cannot answer at all. Both are matched by
             .gitignore's euterpe_ledger.json* and neither reaches the repo.
             take_base() reads the ledger from DISK rather than from anything
             typed, so a base copy is always a state that was saved.

Config:      config/euterpe_config.json (§4) — the two window sizes and the
             duration cap, so they are tuned by editing JSON rather than code.
             That matters more than it looks: a stable taste anchor plus a
             fixed window lets a small model settle into a loop the length of
             the window, and widening it trades that for more misses. It is a
             dial, not a constant to get right once.

Upstream:    awen.py (records plays, reads the views)
Downstream:  euterpe_ledger.json, config/euterpe_config.json

Requires:    json, logging, os, re, shutil, tempfile, time, unicodedata
             (stdlib only).
"""

import json
import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
from typing import Any, Optional

log = logging.getLogger("METIS.euterpe")

# tools/ → app root, next to felhaven.py (§1: anchor to __file__, never cwd).
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEDGER_PATH = os.path.join(_APP_ROOT, "euterpe_ledger.json")
_CONFIG_PATH = os.path.join(_APP_ROOT, "config", "euterpe_config.json")

# How many plays the log keeps. Not config: this bounds a file, it does not tune
# behaviour, and no window can usefully exceed it. Generous next to a 20-song
# no-repeat window, and more generous still since 2026-09-17 — the DJ's own
# picks stopped landing here, so this now fills only as fast as the listener
# chooses songs or edits the list by hand.
_MAX_ENTRIES = 200

# The rolling backup written before a hand edit (see replace_all). One file, not
# a pile: .gitignore matches euterpe_ledger.json* so it never reaches the repo.
_BACKUP_SUFFIX = ".bak"

# The base copy — a restore point the operator takes deliberately, and which
# nothing in this module overwrites on its own. The .bak above cannot serve as
# one: it rolls, so the good state is gone after one more save.
#
# Both suffixes are appended to _LEDGER_PATH at CALL time, never stored as a
# module constant. Tests redirect _LEDGER_PATH, and a path computed at import
# would keep pointing at the real file after they did.
_BASE_SUFFIX = ".base"

# What an empty ledger looks like, as TEXT. raw_text() hands this back when the
# file is absent so the editor always opens on valid JSON to edit rather than a
# blank box that asks the operator to remember the schema.
_EMPTY_DOCUMENT = """{
  "plays": []
}
"""

# Every field record() writes, and what it may be. Derived from one place on
# purpose: this is the shape a hand edit is held to, and record() at the bottom
# of this file is the only thing that creates it.
#
# bool is rejected everywhere rather than listed nowhere, because
# isinstance(True, int) is True and "duration": true would otherwise pass.
_REQUIRED_FIELDS: dict[str, tuple[type, ...]] = {
    "title":    (str,),
    "key":      (str,),
    "video_id": (str, type(None)),
    "url":      (str,),
    "channel":  (str,),
    "duration": (int, float),
    "source":   (str,),
    "at":       (str,),
}

# Fields that may not be blank. The key is the one the DJ actually matches on —
# the anchor and the no-repeat window are both lists of keys, never titles — so
# a row with an empty key is a row that silently does nothing.
_NON_EMPTY_FIELDS = ("title", "key")

# The shipped config's defaults, and the values used when the file is missing or
# malformed. tests/test_euterpe.py asserts these against config/ in lockstep, so
# the two can never drift (§10 — never a hardcoded count, always two live
# sources compared).
_DEFAULTS: dict[str, int] = {
    # Shortest thing that counts as a song. A real ledger collected a 26 s room
    # -echo demo and a 55 s AI-art slideshow, both of which then steered the DJ.
    "min_song_seconds": 90,
    # How far back "don't play this again" reaches, in plays.
    "no_repeat_window": 20,
    # How many of the listener's OWN picks anchor the taste. Deliberately not
    # "the last N plays": after N DJ picks every prior would be the model's own
    # choice and the taste would drift wherever the model wandered.
    "taste_anchor": 5,
    # Longest thing that counts as a song. 15 minutes clears essentially every
    # track while excluding the lore videos, mixes and full albums Morpheus is
    # also used for.
    "max_song_seconds": 900,
}

# Titles that are plainly not songs, matched as case-folded SUBSTRINGS. Config
# rather than code (§4) for one reason: this is a denylist, and every denylist
# is eventually wrong about something. When it refuses a real track, the fix is
# a JSON edit, not a release.
#
# It exists because duration alone does not separate a song from a 282 s gear
# manual. Measured from a real evening: "Moss Echo User Manual", "An In-depth
# Review of Moss Air", "Grunge: A Brief History of a Brief Genre" and a Hatsune
# Miku REACTION & ANALYSIS all passed every other guard.
#
# PHRASES are preferred over bare words, deliberately. "review" alone would
# refuse a song called "Review"; "review of" almost certainly will not. The
# known cost is stated rather than hidden: a real track whose title contains one
# of these is silently unplayable by the DJ, and the operator's recourse is to
# delete the line.
_DEFAULT_NOT_MUSIC: list[str] = [
    "user manual", "review of", "in-depth", "unboxing", "tutorial",
    "how to", "explained", "documentary", "history of", "reaction",
    "interview", "podcast", "lecture", "audiobook", "walkthrough",
]

# YouTube's OWN category, for the things a title cannot catch. The denylist
# above can only ever ban what has already burned somebody: it was written from
# a real evening, and the next offender will be about something else entirely.
# A video about sharpening a chainsaw played in full and was announced as Linkin
# Park — it contains none of the phrases above and four minutes sits comfortably
# inside the duration band, so it passed every check there was.
#
# NARROW on purpose. "Entertainment" and "People & Blogs" are absent and must
# stay absent: a great deal of real music lives in both, and a denylist that
# refuses whole swathes of music to catch one chainsaw is a worse trade than the
# chainsaw. Config for the same reason as the phrases — when it is wrong, the
# fix is a JSON edit.
_DEFAULT_NOT_MUSIC_CATEGORIES: list[str] = [
    "Education", "Howto & Style", "Science & Technology", "News & Politics",
    "Gaming", "Autos & Vehicles", "Pets & Animals", "Sports",
    "Travel & Events",
]

# Where a play came from. "user" is anything Matthew started himself — the
# Morpheus panel, or asking Pythia in chat — and is what the taste anchor reads.
#
# "dj" no longer arrives on its own. Awen wrote one for every pick it made until
# 2026-09-17; it now keeps those in memory for the run and never adds to this
# file, so the ledger is the listener's LIST rather than a log of everything
# that played (see awen.py, "Reads, does not write"). The value stays, and stays
# valid, for two reasons: rows written before that date are still here, and the
# TUNES tab can still type one by hand — "this played, but it is not my taste",
# which counts against the no-repeat window without steering the next pick.
SOURCES = ("user", "dj")

# ── The dedupe key ────────────────────────────────────────────────────────────
# Upload junk, in the forms actually seen on YouTube (measured 2026-09-16).
# Kept CONSERVATIVE on purpose: over-stripping collapses two genuinely different
# recordings into one and silently hides a song from the DJ forever, which is a
# worse failure than an occasional missed repeat.
#
# Note what is NOT here. "version" and "remix" are excluded because an acoustic
# version or a remix is a different recording, not a different upload of the
# same one. "live" IS here, because to a listener the same song in a live take
# is still a repeat — that one is a judgment call, and this is where to change
# it if it turns out to be the wrong one.
_JUNK_WORDS = (r"official|lyrics?|audio|video|remaster(?:ed)?|hd|hq|uhd|"
               r"4k|8k|visualizer|visualiser|vinyl|explicit|clean|live|"
               r"full\s+album|color\s+coded|"
               r"feat\.?|ft\.?|featuring")

# A PARENTHESISED group is stripped only when it contains a junk word, so a
# title whose own name uses brackets — "(I Can't Get No) Satisfaction" — is left
# intact. Square brackets are stripped wholesale: "[4K]", "[HD]", "[Official
# Video]" are what they are used for, and a song title using them is rare.
_PAREN_JUNK = re.compile(r"\(\s*[^()]*\b(?:" + _JUNK_WORDS + r")\b[^()]*\)", re.I)
_BRACKET_JUNK = re.compile(r"\[[^\]]*\]")

# Junk sitting bare in the title, with no brackets at all — measured on
# "Pink Floyd - Another Brick in the Wall Official Music Video (Lyrics In
# Description)". MULTI-WORD phrases only: a bare "lyrics" pattern would maul a
# song actually called "Lyrics", and the phrase form cannot.
_BARE_JUNK = re.compile(
    r"\b(?:official\s+(?:music\s+)?video|official\s+audio|official\s+lyrics?"
    r"|lyrics?\s+video|music\s+video)\b", re.I)

# Every dash YouTube titles use between artist and track, folded to one.
_DASHES = re.compile(r"[‐-―−]")
_SEPARATORS = re.compile(r"[|·•]")
_WHITESPACE = re.compile(r"\s+")

# A featured-artist tail with no brackets at all: "Numb ft. Jay-Z".
# "[feat. X]" was already stripped wholesale by _BRACKET_JUNK and "(feat. X)" is
# reached by _PAREN_JUNK now that _JUNK_WORDS names it, so without this the SAME
# song had two identities decided by which bracket the uploader reached for.
#
# Tempered so it cannot cross a " - ": "Jay-Z feat. Linkin Park - Numb" must
# keep its track. A plain [^-]* would not do, because the featured artist it has
# to survive can contain a hyphen of its own.
#
# The cost, stated rather than hidden: it cannot tell a credit from a place, so
# "Blues in Ft. Worth" keys as "blues in". Tightening it would need to know that
# Worth is a town, which nothing here does. It is the _names_the_song("ADIDAS")
# trade — a rare wrong key bought for a common right one — and the recourse is
# the same, a line typed into TUNES.
_FEAT_TAIL = re.compile(
    r"\s*\b(?:feat\.?|ft\.?|featuring)\s+(?:(?!\s-\s).)*$", re.I)

# Qualifiers YouTube Music appends with a DASH rather than a bracket:
#   "Bohemian Rhapsody - Remastered 2011"
#   "Bother - Live at Rock in Rio"
#
# That form is doubly damaging. The qualifier becomes half the key, so the
# remastered upload is a different song from the plain one; and the dash makes
# _clean() return before the channel-derived artist is applied, so the key loses
# the artist as well. It is also the house style of "- Topic" channels, which
# the DJ has PREFERRED as music evidence since 2026-09-18 — the picks the new
# validation favours were the ones keying worst.
#
# Deliberately NARROWER than _JUNK_WORDS, and that is the difficulty of this
# form rather than caution for its own sake. Inside parentheses a bare "live" is
# safely junk. In a dash tail it might BE the track: "Live and Let Die" and
# "Live Forever" are songs, and stripping their tail would key them as the
# artist alone. So every pattern here either cannot be a song title on its own
# (remaster) or carries the preposition that makes it a venue rather than a
# name (live AT / IN / FROM).
# Only the two qualifiers _JUNK_WORDS ALREADY names, and that limit is the
# point. "radio edit", "acoustic version" and "single version" were here for one
# afternoon and had to come out: _JUNK_WORDS deliberately excludes `version` and
# `remix` because an acoustic version is a different RECORDING, not a different
# upload of the same one — so stripping them from a dash tail contradicted a
# decision written forty lines above, and did it for one spelling only. That
# recreated the very asymmetry _FEAT_TAIL exists to remove: "Dreams - Acoustic
# Version" collapsed into "Dreams" while "Dreams (Acoustic Version)" did not.
# An over-strip is also the worse direction to fail in — it hides a song from
# the DJ for good, where a missed repeat costs one play.
_DASH_TAIL_JUNK = re.compile(
    r"\s-\s(?:"
    r"(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?"
    r"|live\s+(?:at|in|from)\b[^-]*"
    r")\s*$", re.I)

# youtube.com/watch?v=<11>, youtu.be/<11>, /embed/<11>, /shorts/<11>.
_VIDEO_ID = re.compile(
    r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")

# Channel-name furniture, for the artist-prefix rule in normalize(). YouTube's
# auto-generated artist channels are "<Artist> - Topic", and label uploads carry
# VEVO or a trailing "Official".
_CHANNEL_JUNK = re.compile(r"\s*-\s*topic\s*$|vevo|\s+official\s*$", re.I)

# YouTube's auto-generated music channels, which _CHANNEL_JUNK already strips
# from a key. Matched on its own here because it is EVIDENCE rather than noise:
# a "- Topic" channel is generated from a music rights feed, so an upload on one
# is a track with near-certainty. The other two halves of _CHANNEL_JUNK say
# nothing of the kind, which is why this is a second pattern and not a reuse.
_TOPIC_CHANNEL = re.compile(r"\s-\stopic\s*$", re.I)


def normalize(title: str, channel: str = "") -> str:
    """The dedupe key: one song, one string, whatever upload it arrived as.

    Strips bracketed and bare upload junk, folds dash variants and separators,
    collapses whitespace and case-folds. Never raises; a title that is entirely
    junk normalizes to "" and callers treat that as "no key" rather than as a
    match-everything.

    >>> normalize("Fleetwood Mac - Dreams (Official Music Video) [4K]")
    'fleetwood mac - dreams'

    `channel` supplies the ARTIST when the title omits it, which is not a corner
    case: a single measured search for "Fleetwood Mac Dreams" returned four
    uploads titled "Fleetwood Mac - Dreams (...)" and one titled just "Dreams
    (2004 Remaster)", on the channel "Fleetwood Mac". Without this the fifth
    keys as "dreams", matches none of the others, and the DJ can play the same
    song twice.

    The prefix is added ONLY when the normalized title has no "artist - track"
    dash of its own, so it can sharpen a vague key but never overwrite a good
    one. That also makes it safe: it can turn a miss into a match, and it cannot
    turn two different songs into the same key, because that would need them to
    share both a title and an artist.

    >>> normalize("Dreams (2004 Remaster)", "Fleetwood Mac")
    'fleetwood mac - dreams'
    """
    return _clean(title, channel, fold=True)


def pretty(title: str, channel: str = "") -> str:
    """The same string as normalize(), with the CASE kept — a display name.

    >>> pretty("In The End [Official HD Music Video] - Linkin Park")
    'In The End - Linkin Park'

    Exists so the DJ can say what is playing without asking the model to
    remember it. Measured 2026-09-17: handed the resolved title in a tool
    result, gemma4:e2b still introduced a different Linkin Park song. A name
    that has to be right is not a thing to ask a small model for when the
    string is already in hand.

    Shares every rule with normalize() through _clean(), rather than repeating
    the regexes: a display name that stripped differently from the key would
    quietly show one song and file another.
    """
    return _clean(title, channel, fold=False)


def _clean(title: str, channel: str, *, fold: bool) -> str:
    """Strip upload junk, fold separators, and borrow the artist from the
    channel when the title has none. `fold` case-folds for a dedupe key; the
    display form keeps the case it found.

    Every comparison inside is case-insensitive either way, so the two forms
    make the SAME decisions and differ only in the characters they return.
    """
    # FIRST, before any regex sees the string, so every rule below works on one
    # form. casefold() folds CASE and nothing else — it does not touch Unicode
    # normal form, so "ü" written as one codepoint (U+00FC) and the same letter
    # written as "u" plus a combining diaeresis (U+0075 U+0308) survive it as
    # two different strings that no amount of folding will reconcile.
    #
    # Not hypothetical, and not rare either: measured 2026-09-20 on the live
    # ledger, which held "Altın Gün - Goca Dünya" in NFD beside "Michael Bublé"
    # in NFC. mpv's media-title and yt-dlp's search result are written by
    # different producers, so one song arrived spelled two ways, keyed twice,
    # and same_song() said no — which filed a DJ pick as the listener's taste,
    # let the DJ replay a song it had just played, and would have written the
    # same song to the ledger twice. Three bugs, one missing line.
    #
    # NFC rather than NFD because it is what JSON from the web carries, what a
    # text box produces, and what every ledger row but one was already in — so
    # this folds the odd one in rather than re-keying the file. Stored keys are
    # NOT re-derived (see replace_all): the one NFD row keeps the key it has and
    # simply stops being able to match itself, which costs one stale no-repeat
    # entry and no correctness.
    text = unicodedata.normalize("NFC", title or "")
    text = _BRACKET_JUNK.sub(" ", text)
    text = _PAREN_JUNK.sub(" ", text)
    text = _BARE_JUNK.sub(" ", text)
    text = _DASHES.sub("-", text)
    text = _SEPARATORS.sub("-", text)
    text = _WHITESPACE.sub(" ", text)
    # Both AFTER the dash folding, so every dash YouTube uses has already become
    # "-", and both BEFORE the strip and the early return below: a qualifier
    # left on the tail is exactly what stops the channel-derived artist ever
    # arriving.
    text = _FEAT_TAIL.sub("", text)
    while True:
        shorter = _DASH_TAIL_JUNK.sub("", text)
        if shorter == text:
            break                  # loops for "Track - Remastered - Live at X"
        text = shorter
    # Leading/trailing punctuation is what stripping a trailing "(Official
    # Video)" leaves behind — "Dreams -" must key the same as "Dreams".
    key = text.strip(" -_.,:;/")
    if fold:
        key = key.casefold()
    if not key or " - " in key:
        return key

    artist = _CHANNEL_JUNK.sub(" ", channel or "")
    artist = _WHITESPACE.sub(" ", artist).strip(" -_.,:;/")
    if fold:
        artist = artist.casefold()
    if not artist or artist.casefold() == key.casefold():
        return key
    # The title may already START with the artist without a dash ("Queen
    # Bohemian Rhapsody"); prefixing again would key it twice.
    if key.casefold().startswith(artist.casefold() + " "):
        return artist + " - " + key[len(artist) + 1:]
    return artist + " - " + key


def same_song(key_a: str, key_b: str) -> bool:
    """Are these two keys the same song? ONE judgment, three callers — the
    guard, record()'s move-on-replay, and replace_all()'s duplicate check. If
    they could disagree, a song the guard refused could still be filed twice.

    Order-insensitive across the dash, because YouTube ships the same song both
    ways round and normalize() cannot tell which half is the artist:

        Linkin Park - In The End (Official Music Video)
        In The End [Official HD Music Video] - Linkin Park

    Measured 2026-09-17 from a real mpv log — those are the two uploads that got
    played back to back. Equality on the keys says no; this says yes.

    The comparison is what moved, NOT the key. Stored keys keep the order they
    arrived in, so TUNES and the model's do-not-repeat list still read like
    titles rather than like an index, and nothing on disk needed re-keying.

    Two different songs colliding would need one's artist to be the other's
    track AND vice versa, which is not a thing that happens in music.
    """
    return _halves(key_a) == _halves(key_b) if key_a and key_b else False


def _halves(key: str) -> tuple[str, ...]:
    """A key with the order AND the case taken out of it. Sorted parts, so the
    comparison is a plain tuple equality rather than a set of special cases.

    The fold is not belt and braces. record() case-folds every key it writes, so
    for a long time nothing here could meet a capitalised one — but
    replace_all() deliberately does not re-derive what the operator typed, and
    TUNES is a text box. Measured on the live ledger: two rows four hours apart,
    "Cage The Elephant - ain't no rest for the wicked" and its own folded form,
    both accepted by _validate(), both copied into .bak and .base, and both
    handed to the model as separate songs to avoid.
    """
    parts = tuple(part for part in
                  (piece.strip().casefold() for piece in key.split(" - "))
                  if part)
    return tuple(sorted(parts))


def _as_seconds(duration: Optional[float]) -> Optional[float]:
    """`duration` as a float, or None when it is absent or not a number.

    Shared by is_a_song() and record() so the two cannot disagree about what a
    duration even IS — and so record() has a value a type checker can narrow,
    rather than re-deriving one is_a_song has already proved."""
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


def has_music_evidence(details: Optional[dict[str, Any]]) -> bool:
    """Does this video's own metadata say it is music?

    Three signals, any one of which is enough, in descending order of how often
    they are present: YouTube's own "Music" category, a populated track AND
    artist pair (YouTube Music's rights metadata, which nothing else carries),
    and a "- Topic" channel.

    POSITIVE evidence only. False means "nothing here says music", never "this
    is not music" — live sets, covers and indie uploads routinely carry none of
    it, which is exactly why this SELECTS a candidate rather than gating one
    (awen._guarded_play_music). Treating absence as proof would narrow the DJ to
    mainstream Topic-channel uploads and call it an improvement.

    Never raises: a details dict it cannot read is simply no evidence.
    """
    if not isinstance(details, dict) or "error" in details:
        return False
    categories = details.get("categories")
    if isinstance(categories, list):
        if any(str(name).strip().casefold() == "music" for name in categories):
            return True
    track = str(details.get("track") or "").strip()
    artist = str(details.get("artist") or "").strip()
    if track and artist:
        return True
    return bool(_TOPIC_CHANNEL.search(str(details.get("channel") or "")))


def not_music_categories() -> list[str]:
    """The CATEGORY denylist, from config, falling back to the shipped default.

    Its own function for the same reason as not_music_phrases(): it is a list,
    and folding it into settings() would make every caller handle a union type
    for the sake of one key. An explicitly empty list disables it.
    """
    raw = _raw_config().get("not_music_categories")
    if raw is None:
        return list(_DEFAULT_NOT_MUSIC_CATEGORIES)
    if not isinstance(raw, list):
        log.warning("euterpe: not_music_categories is not a list — using the default")
        return list(_DEFAULT_NOT_MUSIC_CATEGORIES)
    return [str(name).strip() for name in raw if str(name).strip()]


def is_a_song(title: str, duration: Optional[float],
              details: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Is this a SONG? Returns None when it is, or a reason slug when it is not.

    One judgment in one place, because two places were already asking it — the
    ledger deciding what counts as a prior, and Awen's guard deciding what the
    DJ may play — and they must not be able to disagree. A thing the DJ is
    forbidden to play but the ledger happily stores would become a prior the DJ
    could never match, and the reverse would let the DJ play what the ledger
    then refuses to remember.

    Three tests, all config-driven (§4):
      - too short: below min_song_seconds. A 26 s room-echo demo is not a track.
      - too long, or NO DURATION AT ALL: above max_song_seconds, or a live
        stream, which yt-dlp reports as None and which never ends.
      - not music by title: see _DEFAULT_NOT_MUSIC.
      - not music by CATEGORY, when `details` are supplied.

    `details` is morpheus.describe()'s full metadata, when the caller has paid
    for it. Two properties hold and both are load-bearing:

      - WITHOUT it the answer is byte-identical to before. The ledger calls this
        with two arguments and always will — _note_playing records a listener's
        play from an mpv title and a duration, with no URL and therefore no
        metadata, ever. So the judgment the ledger and the guard share is shared
        because it is literally the same code.
      - WITH it, details can only ADD a refusal, never remove one, and the
        category test runs LAST so no existing slug can change. A video that
        YouTube files under Music and that runs thirty seconds is still
        too_short. The tempting mutation is to let good metadata approve a
        track; it must not.

    The fourth test, when details are present:
      - not music by CATEGORY: YouTube files it under something instructional
        or otherwise non-musical AND nothing in its metadata says music. Both
        halves are required — a Music-metadata track miscategorised as
        Education is still a track.

    Never raises; a title it cannot read is simply not a song.
    """
    values = settings()
    if duration is None:
        return "no_duration"
    seconds = _as_seconds(duration)
    if seconds is None:
        return "bad_duration"
    if seconds < values["min_song_seconds"]:
        return "too_short"
    if seconds > values["max_song_seconds"]:
        return "too_long"
    lowered = (title or "").casefold()
    for phrase in not_music_phrases():
        if phrase in lowered:
            return "not_music"
    if _is_filed_as_not_music(details):
        return "not_music_category"
    return None


def _is_filed_as_not_music(details: Optional[dict[str, Any]]) -> bool:
    """Does YouTube file this under something that is not music, with nothing in
    its metadata to say otherwise?

    Split out so is_a_song() reads as four tests rather than four tests and a
    paragraph, and so the "and nothing says music" half cannot be dropped by
    someone simplifying the condition — that half is what keeps a miscategorised
    real track playable.
    """
    if not isinstance(details, dict) or "error" in details:
        return False
    categories = details.get("categories")
    if not isinstance(categories, list):
        return False
    if has_music_evidence(details):
        return False
    denied = {name.casefold() for name in not_music_categories()}
    return any(str(name).strip().casefold() in denied for name in categories)


def video_id(url: Optional[str]) -> Optional[str]:
    """The YouTube video id inside `url`, or None. Never raises."""
    if not url:
        return None
    found = _VIDEO_ID.search(url)
    return found.group(1) if found else None


# ── Config ────────────────────────────────────────────────────────────────────

def not_music_phrases() -> list[str]:
    """The title denylist, from config, falling back to the shipped default.

    Separate from settings() because it is the one value that is not an integer,
    and folding it in would make every caller of settings() handle a union type
    for the sake of one key."""
    raw = _raw_config().get("not_music")
    if raw is None:
        return list(_DEFAULT_NOT_MUSIC)
    if not isinstance(raw, list):
        log.warning("euterpe: not_music is not a list — using the default")
        return list(_DEFAULT_NOT_MUSIC)
    phrases = [str(p).casefold().strip() for p in raw if str(p).strip()]
    return phrases          # an explicitly EMPTY list disables the denylist


def _raw_config() -> dict[str, Any]:
    """The config file as parsed, or {} — shared by settings() and
    not_music_phrases() so a malformed file is reported the same way to both."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as handle:
            raw: Any = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.warning("euterpe: config unreadable, using defaults: %s", exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("euterpe: config root is not an object — using defaults")
        return {}
    return raw


def settings() -> dict[str, int]:
    """The tuning values, with _DEFAULTS filled in for anything missing.

    Read fresh each call (the machine_spirit.effective_prompt() discipline), so
    editing the JSON takes effect on the next read with no restart. Fail-soft
    per field rather than wholesale: one bad value falls back to its own default
    and the rest of the file is still honoured. Never raises.
    """
    values = dict(_DEFAULTS)
    raw = _raw_config()
    for key in _DEFAULTS:
        if key not in raw:
            continue
        try:
            number = int(raw[key])
        except (TypeError, ValueError) as exc:
            log.warning("euterpe: bad %s in config, using default: %s", key, exc)
            continue
        if number < 1:
            log.warning("euterpe: %s must be >= 1, using default", key)
            continue
        values[key] = number
    return values


# ── Persistence ───────────────────────────────────────────────────────────────

def entries() -> list[dict[str, Any]]:
    """The whole log, oldest first. [] when the file is absent or unreadable —
    an empty history, never an exception. Rows that are not objects are dropped
    rather than handed on, so one hand-edited line cannot break every reader."""
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as handle:
            raw: Any = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.warning("euterpe: ledger unreadable, starting empty: %s", exc)
        return []

    if not isinstance(raw, dict):
        log.warning("euterpe: ledger root is not an object — starting empty")
        return []
    plays = raw.get("plays")
    if not isinstance(plays, list):
        return []
    return [row for row in plays if isinstance(row, dict)]


def raw_text() -> str:
    """The ledger file's literal text, for the editor to show.

    Deliberately NOT json.dumps(entries()): that would silently reformat a hand
    edit and drop any row entries() rejects, so the operator would be editing a
    cleaned-up copy of their file rather than their file. The editor's whole
    value is that what you see is what is on disk.

    The canonical empty document when the file is absent or unreadable, never
    "" — an editor that opens on a blank box asks the operator to remember the
    schema. A file that is present but unreadable cannot be backed up either,
    so replace_all() refuses the save that would follow (see _backup).
    """
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return _EMPTY_DOCUMENT
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("euterpe: ledger unreadable, editor opens empty: %s", exc)
        return _EMPTY_DOCUMENT


def stamp() -> float:
    """The ledger's mtime, 0.0 when it is absent. The editor's staleness token.

    Awen records plays on a 2 s tick, so a tab left open while a song ends is
    holding text that no longer matches the file, and saving it would clobber
    that play without a word. The editor keeps the value it loaded at and
    compares it back before writing.

    Compared for EQUALITY only. Nothing here reasons about which of two times is
    later: "different from what I loaded" is the whole question, and it is the
    one question a clock change cannot answer wrongly.

    Relies on _save() forcing the mtime forward on every write (see
    _bump_mtime) rather than trusting the OS's own write-time stamp — two
    _save() calls close enough together can otherwise be assigned the SAME
    mtime, and stamp() would then read a save that landed as "unchanged".
    """
    try:
        return os.path.getmtime(_LEDGER_PATH)
    except OSError:
        return 0.0


# How far a write nudges the ledger's mtime forward when the write's own
# OS-assigned timestamp did not already clear the PREVIOUS one. Comfortably
# above the ~100ns granularity NTFS actually stores, so the step survives the
# round trip through os.utime() and back through stamp().
_MIN_STAMP_STEP = 0.001


def _bump_mtime(previous: float) -> None:
    """Force the ledger's mtime strictly past `previous`, measured 2026-09-19:
    Awen's record() and a hand SAVE landing close enough together could be
    timestamped identically by the filesystem — the write-time clock's own
    resolution is coarser than the time between two writes from the same
    process — and stamp() compares for EQUALITY, so an unmoved mtime read as
    "nothing changed" and let a save through that should have been refused.

    Sampling `time.time()` again does not fix this on its own: if the clock's
    resolution is what caused the collision, a second reading close behind the
    first can round to the very same tick. Only an explicit floor against the
    PREVIOUS stamp guarantees a move, which is why this takes it as an
    argument rather than re-deriving it.

    Best-effort: an OSError here leaves the OS's own timestamp in place, which
    is exactly today's (racy) behaviour, not a regression.
    """
    now = time.time()
    if now <= previous:
        now = previous + _MIN_STAMP_STEP
    try:
        os.utime(_LEDGER_PATH, (now, now))
    except OSError as exc:
        log.warning("euterpe: could not bump the ledger's mtime: %s", exc)


def _backup() -> bool:
    """Copy the ledger aside before a whole-file replace. True when there is a
    net, or nothing to protect; False when one was wanted and could not be made.

    An absent ledger is a SUCCESS, not a failure: a first save has no previous
    content to lose. Anything else is refused rather than taken blind — this
    copy is the undo the module otherwise doesn't have, so a save without it is
    exactly the situation the operator asked not to be in.
    """
    if not os.path.exists(_LEDGER_PATH):
        return True
    try:
        shutil.copy2(_LEDGER_PATH, _LEDGER_PATH + _BACKUP_SUFFIX)
        return True
    except OSError as exc:
        log.error("euterpe: could not back up the ledger before a hand edit: %s", exc)
        return False


def _save(plays: list[dict[str, Any]]) -> bool:
    """Write the log atomically. Returns success rather than raising — see the
    "Never raises" docstring section for why this one differs from plutus.

    tempfile in the SAME directory then os.replace (§9): a crash mid-write
    leaves the old file intact, and same-directory is what makes the replace
    atomic rather than a cross-volume copy.

    `previous` is read BEFORE the write so _bump_mtime has the one thing it
    needs to guarantee this write's stamp differs from it — see stamp() and
    _bump_mtime for why the OS's own write-time is not trusted to do that
    alone.
    """
    previous = stamp()
    try:
        directory = os.path.dirname(_LEDGER_PATH)
        os.makedirs(directory, exist_ok=True)
        handle_fd, temp_path = tempfile.mkstemp(
            dir=directory, prefix=".euterpe-", suffix=".tmp")
    except OSError as exc:
        log.error("euterpe: could not open the ledger for writing: %s", exc)
        return False

    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump({"plays": plays}, handle, indent=2, ensure_ascii=False)
        os.replace(temp_path, _LEDGER_PATH)
        _bump_mtime(previous)
        return True
    except (OSError, ValueError, TypeError) as exc:
        log.error("euterpe: could not write the ledger: %s", exc)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return False


# ── Mutation (the only write path) ────────────────────────────────────────────

def record(title: str,
           *,
           url: Optional[str] = None,
           channel: str = "",
           duration: Optional[float] = None,
           source: str = "user") -> Optional[dict[str, Any]]:
    """Append one CONFIRMED play and persist. Returns the stored entry, or None
    when the play was declined or the write failed.

    "Confirmed" is the caller's responsibility and it is the important word:
    this must be called for media that actually existed (Morpheus's
    time-pos/duration evidence), never for a play() that was merely fired. A
    load that failed never produced a song and must never become a prior.

    A song already on the list is MOVED, not duplicated — the old row is dropped
    and the new one appended (see "One row per song").

    Declined, returning None and writing nothing:
      - a blank title, or one that normalizes to nothing
      - no duration, or one over max_song_seconds (see "Duration")
      - an unknown source
    None is not an error — the log line says which of these it was at debug
    level, because an excluded hour-long lore video is routine, not a fault.

    Never raises (see "Never raises").
    """
    if source not in SOURCES:
        log.warning("euterpe: unknown source %r — not recorded", source)
        return None

    key = normalize(title, channel)
    if not key:
        log.debug("euterpe: %r has no usable title key — not recorded", title)
        return None

    # ONE judgment, shared with Awen's guard (see is_a_song). A thing the DJ may
    # not play but the ledger stores would become a prior the DJ could never
    # match; the reverse would let it play what the ledger refuses to remember.
    why = is_a_song(title, duration)
    seconds = _as_seconds(duration)
    if why is not None or seconds is None:
        log.debug("euterpe: %r is not a song (%s) — not recorded", title, why)
        return None

    entry: dict[str, Any] = {
        "title":    title,
        "key":      key,
        "video_id": video_id(url),
        "url":      url or "",
        "channel":  channel or "",
        "duration": seconds,
        "source":   source,
        "at":       time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    plays = entries()
    # ONE ROW PER SONG (see "One row per song"). Playing something already on
    # the list MOVES it rather than adding a second copy: the old row is dropped
    # and the new one appended, so the list stays as long as the number of
    # distinct songs and the replay counts as the fresh signal it is.
    #
    # Matched on the normalized key alone, not also the video id. The key is
    # what identity MEANS here — collapsing one song's many uploads is the
    # entire reason normalize() exists — and a second test on the id could only
    # ever split a row the key had already joined.
    plays = [row for row in plays
             if not same_song(str(row.get("key") or ""), key)]
    plays.append(entry)
    # Bound the log from the FRONT, so the newest survive. Done before the write
    # rather than on read: a file that grows without limit is the problem, and
    # trimming only on read would never shrink it.
    del plays[:-_MAX_ENTRIES]
    if not _save(plays):
        return None
    return entry
# ── Whole-file replace (the operator's write path, not the model's) ───────────

def replace_all(text: str) -> Optional[str]:
    """Validate `text` as a whole ledger and write it. None on success, an error
    slug on refusal — and on ANY refusal the file on disk is untouched.

    This is the TUNES editor's save, and the only caller. See the "No delete"
    docstring section for why a whole-file replace does not contradict the rule
    that there is no clear(): the model still has no write handle, because this
    module is still out of pythia._TOOL_MODULES.

    Refusals, in the §2 error vocabulary, checked in this order so the operator
    is told about the first thing wrong rather than the last:

      invalid_json: line N   the text is not JSON; N is json's own line number
      bad_root               not an object, or "plays" is not a list
      bad_row: N             row N (1-based, as a human counts) is not an object,
                             is missing a field, has one of the wrong type, or
                             leaves the title or key blank
      bad_source: N          row N's source is neither "user" nor "dj"
      duplicate_row: ...     two rows are the same song; both are named
      backup_failed          the .bak could not be written, so the net is not
                             there and the save is not taken
      write_failed           the atomic write itself failed

    Stricter than entries(), on purpose and in the opposite direction. entries()
    drops a bad row and carries on, because a reader on a Kairos tick must never
    stop the music over one malformed line. An editor has a human in front of it
    who can fix the line, so it says which one rather than quietly eating it.

    What it does NOT do is rewrite what you typed. Keys are not re-derived from
    titles: a normalize() pass here would silently overwrite a deliberate key
    edit and touch rows the operator never looked at, and then what the editor
    showed would stop being what the file holds.
    """
    plays, error = _validate(text)
    if error is not None:
        return error
    assert plays is not None      # _validate returns one or the other

    # Before the write, never after: the backup is only a backup while it still
    # holds the OLD content.
    if not _backup():
        return "backup_failed"

    # The same bound record() applies (see there). The cap exists to stop the
    # file growing without limit, and an editor that walked around it would be a
    # hole in the only thing keeping it finite.
    del plays[:-_MAX_ENTRIES]
    if not _save(plays):
        return "write_failed"
    return None


def _validate(text: str) -> tuple[Optional[list[dict[str, Any]]], Optional[str]]:
    """(plays, None) when `text` is a whole valid ledger, (None, slug) when it
    is not. ONE judgment, in the is_a_song() spirit: the editor's save and the
    base copy both ask it, so they cannot end up disagreeing about what a ledger
    is and leaving a base copy that SAVE would then refuse to restore.
    """
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid_json: line {exc.lineno}"

    if not isinstance(raw, dict):
        return None, "bad_root"
    plays = raw.get("plays")
    if not isinstance(plays, list):
        return None, "bad_root"

    for index, row in enumerate(plays, start=1):
        if not isinstance(row, dict):
            return None, f"bad_row: {index}"
        for field, types in _REQUIRED_FIELDS.items():
            if field not in row:
                return None, f"bad_row: {index}"
            value = row[field]
            if isinstance(value, bool) or not isinstance(value, types):
                return None, f"bad_row: {index}"
        for field in _NON_EMPTY_FIELDS:
            if not str(row[field]).strip():
                return None, f"bad_row: {index}"
        if row["source"] not in SOURCES:
            return None, f"bad_source: {index}"

    # One row per song here too, or the rule record() keeps would be one a hand
    # edit could walk straight past. REFUSED rather than collapsed: this is the
    # function that promises not to rewrite what was typed (see replace_all),
    # and silently dropping a row the operator can see on screen is exactly the
    # kind of quiet helpfulness that makes a file stop matching its editor.
    # Naming both rows is the point — "there is a duplicate somewhere in 60
    # lines" is not an actionable message.
    # Keyed on _halves, not the key itself, so the same song written both ways
    # round the dash is caught here exactly as same_song() catches it in the
    # guard. The message quotes the key as TYPED, because that is what the
    # operator has to find in the box.
    seen: dict[tuple[str, ...], tuple[int, str]] = {}
    for index, row in enumerate(plays, start=1):
        key = str(row["key"]).strip()
        halves = _halves(key)
        if halves in seen:
            first, first_key = seen[halves]
            both = (f"both {key!r}" if first_key == key
                    else f"{first_key!r} and {key!r}")
            return None, f"duplicate_row: plays {first} and {index} are {both}"
        seen[halves] = (index, key)

    return plays, None


# ── The base copy (a restore point the rolling .bak cannot be) ────────────────

def base_snapshot() -> Optional[dict[str, Any]]:
    """The base copy as {"text", "plays", "at"}, or None when there isn't one.

    One call rather than three, so the panel gets the text to restore AND the
    caption's facts from a single read and never has to parse JSON itself.

    "plays" is None when the file will not PARSE. That is deliberately not
    treated as "no base copy": the text is still handed back so it can be
    opened in the editor and fixed, and SAVE's own validation is the thing that
    refuses to commit it. A restore point you cannot even look at is worse than
    one you can see is broken.

    Counted from the parse, NOT from _validate(). A base copy taken before a
    rule existed can be perfectly readable and still fail that rule — the first
    one this project made is 59 legible rows with one duplicate in it — and
    calling that "unreadable" in the caption would be a plain lie about a file
    the operator can open and read. What a rule change costs is a refusal at
    SAVE, naming the rows; it is not this function's business to pre-empt it.
    """
    path = _LEDGER_PATH + _BASE_SUFFIX
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        at = os.path.getmtime(path)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("euterpe: base copy unreadable: %s", exc)
        return None

    try:
        raw: Any = json.loads(text)
        rows = raw["plays"] if isinstance(raw, dict) else None
        count = len(rows) if isinstance(rows, list) else None
    except (json.JSONDecodeError, ValueError):
        count = None
    return {"text": text, "plays": count, "at": at}


def take_base() -> Optional[str]:
    """Make the ledger ON DISK the new base copy. None on success, a slug on
    refusal — no_ledger, or one of _validate's, or write_failed.

    From disk, never from the editor's text, and that is the whole point of the
    distinction: a base copy is a state that was actually validated and saved,
    not one that was merely typed. The panel refuses the button while the box
    has unsaved edits rather than quietly taking the older state.

    Written through a temp file and os.replace like every other write here. A
    half-copied base is worse than no base, because it looks like a restore
    point right up until the moment it is needed.
    """
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return "no_ledger"
    except (OSError, UnicodeDecodeError) as exc:
        log.error("euterpe: could not read the ledger to base it: %s", exc)
        return "write_failed"

    _, error = _validate(text)
    if error is not None:
        return error

    destination = _LEDGER_PATH + _BASE_SUFFIX
    try:
        directory = os.path.dirname(destination)
        handle_fd, temp_path = tempfile.mkstemp(
            dir=directory, prefix=".euterpe-base-", suffix=".tmp")
        with os.fdopen(handle_fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp_path, destination)
        return None
    except OSError as exc:
        log.error("euterpe: could not write the base copy: %s", exc)
        return "write_failed"


# ── Derivation (read-only views over the log) ─────────────────────────────────

def recent(limit: Optional[int] = None,
           plays: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """The last `limit` plays, oldest first. Defaults to the no-repeat window.

    `plays` lets a caller fold several views over ONE read instead of re-reading
    the file per view — the plutus.positions(events) idiom.
    """
    rows = entries() if plays is None else plays
    window = settings()["no_repeat_window"] if limit is None else limit
    return rows[-window:] if window > 0 else []


def recent_titles(limit: Optional[int] = None,
                  plays: Optional[list[dict[str, Any]]] = None) -> list[str]:
    """The no-repeat window as NORMALIZED titles, oldest first, de-duplicated
    while keeping order.

    Normalized rather than raw, and that is not only tidiness: these go into the
    DJ's per-turn message, where they are shorter, cleaner, and a title that
    reads like an instruction has less room to work.
    """
    seen: list[str] = []
    for row in recent(limit, plays):
        key = str(row.get("key") or normalize(str(row.get("title", ""))))
        # same_song(), never `key not in seen`. A plain membership test
        # compares raw strings, so it could disagree with the guard about what
        # one song is — and this is the list the MODEL reads, so the
        # disagreement was visible as one song named twice in its own
        # do-not-repeat list.
        if key and not any(same_song(key, other) for other in seen):
            seen.append(key)
    return seen


def was_recently_played(title: str,
                        url: Optional[str] = None,
                        channel: str = "",
                        limit: Optional[int] = None,
                        plays: Optional[list[dict[str, Any]]] = None) -> bool:
    """Is this song inside the no-repeat window? By video id OR normalized
    title — see "Dedupe" for why neither alone is enough.

    The id is checked first and only when BOTH sides have one: a stored None id
    must not match an absent one, or every panel-started play would collide with
    every other.
    """
    rows = recent(limit, plays)
    wanted_id = video_id(url)
    if wanted_id is not None:
        for row in rows:
            if row.get("video_id") == wanted_id:
                return True
    key = normalize(title, channel)
    if not key:
        return False
    return any(same_song(str(row.get("key")
                             or normalize(str(row.get("title", "")))), key)
               for row in rows)


def taste_anchor(limit: Optional[int] = None,
                 plays: Optional[list[dict[str, Any]]] = None) -> list[str]:
    """The listener's OWN recent picks, normalized, oldest first.

    Reads `source == "user"` across the WHOLE log, not just the no-repeat
    window: after a long DJ run there may be no user pick inside the window at
    all, and an empty anchor is what lets the taste drift.

    Returns [] when nothing has ever been played by hand — Awen treats that as
    "no vibe yet" and says so rather than guessing.
    """
    rows = entries() if plays is None else plays
    window = settings()["taste_anchor"] if limit is None else limit
    if window <= 0:
        return []
    picked: list[str] = []
    for row in reversed(rows):
        if row.get("source") != "user":
            continue
        key = str(row.get("key") or normalize(str(row.get("title", ""))))
        # The same hole, second site. Five slots decide what the DJ matches
        # against, so a duplicate pair narrowed the taste by 20% for free.
        if key and not any(same_song(key, other) for other in picked):
            picked.append(key)
        if len(picked) >= window:
            break
    return list(reversed(picked))


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[Euterpe] ledger:  {_LEDGER_PATH}")
    print(f"[Euterpe] config:  {_CONFIG_PATH}")
    print(f"[Euterpe] settings: {settings()}")
    rows = entries()
    print(f"[Euterpe] {len(rows)} play(s) logged")
    for row in rows[-10:]:
        print(f"  [{row.get('source')}] {row.get('title')!r} "
              f"({row.get('duration')}s) -> {row.get('key')!r}")
    print(f"[Euterpe] no-repeat window: {recent_titles()}")
    print(f"[Euterpe] taste anchor:     {taste_anchor()}")
    # Normalization is the part worth eyeballing — these are real YouTube
    # titles, measured 2026-09-16, and all five are the same song.
    print("[Euterpe] normalize() on one search's worth of uploads:")
    for sample, chan in (
            ("Fleetwood Mac - Dreams (Official Music Video) [4K]", "Fleetwood Mac"),
            ("Dreams (2004 Remaster)", "Fleetwood Mac"),
            ("Fleetwood Mac - Dreams (Official Audio)", "Fleetwood Mac"),
            ("Fleetwood Mac - Dreams (Lyrics)", "7clouds"),
            ("Fleetwood Mac - Dreams (Official Vinyl Video)", "RHINO")):
        print(f"  {sample!r}  [{chan}]\n    -> {normalize(sample, chan)!r}")
