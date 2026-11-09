"""
awen.py — Auto-DJ
==================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         When a song ends with Auto-DJ on, choose and play the next one and
             introduce it. Pythia speaks the introduction: her first
             unprompted job — nobody asks for a turn, and nobody is reading
             the transcript while it happens.

Contract:    A flavor §2's table did not have: a POLLED ACTOR.

               fetch()  — the Kairos entry point, and never raises. Observes
                          what is playing, notes it, and when the moment is
                          right takes a DJ turn. Returns a payload for the
                          rider to speak, or a bare state dict.

                          The turn and the sentence about it are DIFFERENT
                          ticks: the tick that picks a song says nothing, and
                          the tick that hears it start does the announcing.
                          See "Announcing".

Reads, does  The ledger (tools/euterpe.py) is the LISTENER'S list — the songs
not write:   they chose, which they curate in Vox Array's TUNES tab. This job
             reads it for the taste anchor and never adds to it. Its own picks
             go to _session_plays, in memory, for this run only. A job that
             wrote to the list it reads from would be a feedback loop, and one
             already happened: until 2026-09-16 a labelling bug filed the DJ's
             own picks as the listener's, a gear-manual video became "what
             Matthew likes", and the DJ dutifully found more gear manuals.
             Removing the write removes the shape of that failure, not just the
             one bug. The listener's OWN plays are still recorded here — this
             module is the only thing watching mpv — including with the flag
             off, or switching it on the first time would find no taste at all.

             WHOSE song it is, is decided by the video id mpv reports as loaded
             (_whose_song), not by comparing two spellings of a title. The old
             test compared euterpe.normalize() over yt-dlp's search result with
             the same over mpv's media-title — two strings with different
             authors — and it closed the loop a second time on 2026-09-20, when
             one upload arrived NFC on one side and NFD on the other. And a
             song that CANNOT be attributed is filed as the DJ's, deliberately:
             a wrong "dj" costs one missed taste signal for one run, a wrong
             "user" is a permanent row in the list this job reads to decide what
             to play next. The two ways of being wrong are not the same size, so
             the doubt goes the cheap way.
               enabled() / set_enabled() / toggle()
                        — the on/off flag. Runtime only, off at every launch.

             What makes it a new flavor rather than a watcher: a
             dashboard-only watcher REPORTS, and this one ACTS. It plays audio
             and spends the model, on its own tick, with no user action behind
             it. Everything else in this stack either answers a question or
             draws a panel; nothing else decides to do something. That is worth
             naming so the next module of this shape knows what it is joining,
             and it is the reason so much of this file is about when NOT to
             fire. See CONVENTIONS §2 and §12.

             fetch() never raising is load-bearing rather than stylistic (the
             morpheus.fetch() precedent, §12). "Nothing is playing" and "the
             model was busy" are normal states, not faults, and Kairos's retry
             ladder must never engage on them — a ladder that pulls the next
             attempt forward is meaningless for a job that is waiting on a song
             to finish.

Speaking:    This module does NOT speak. It returns text; the GUI decides when
             it is heard, exactly as HomePanel already decides for chat answers
             (home_panel.py, *Narration*). Kairos hands the payload to the
             registered rider on the MAIN thread, which is what keeps a worker
             thread away from Calliope and from Tk.

Announcing:  An introduction is HELD until the song is audible, and released by
             _note_playing on the tick it becomes so.

             Because a turn cannot both play a song and truthfully announce it.
             morpheus.play() sends a loadfile and returns; whether that URL
             opens is not known until yt-dlp has finished with it, seconds
             later, which is the fact morpheus._judge_pending_load is built
             around. Measured 2026-09-17: the stream failed to open, mpv logged
             "finished playback, no audio or video data played", and the intro
             had already been read out. A listener was told what they were
             about to hear and then heard nothing.

             It is held ON _pending_pick rather than in a global of its own, so
             it cannot outlive the pick it describes: everything that already
             ends a pick — the load failing, mpv vanishing, the listener
             starting something else — drops the sentence with it, and no new
             rule had to be written for any of those. _note_playing's
             same-song return means it can only be released once.

             The four checks that decide WHAT is said still run in the turn
             (see _take_a_turn). Holding is about when a true thing is said,
             never about whether it was checked.

             Cost, stated rather than hidden: the line now lands a beat AFTER
             the music starts instead of over the gap before it. That is what
             radio sounds like, and Harmonia ducks mpv for narration anyway
             (morpheus.duck), so the song dips under the sentence and comes
             back up. The alternative is announcing songs that do not exist.

             Considered and rejected, 2026-09-19: releasing at the POP instead,
             now that the queue means a popped entry is already guarded,
             deep-checked and URL-resolved rather than a fresh guess. The
             argument is real but proves less than it looks like — it shows the
             load is now UNLIKELY to fail, not that a failure stops mattering.
             _observe's stillborn path recovers from one (counts it, re-arms
             _want_pick) but cannot un-speak a sentence Calliope has already
             started reading; TTS has no retraction. Moving the release earlier
             would not remove the risk bcc10d4 was measured against, only make
             it rarer — and "rarer" is not the invariant this module promises.
             What DID change is when the taper notices the same event: see
             "Taper" below. That gets the sound this was chasing — the new song
             rising as the line begins, rather than both landing at once —
             without moving what "played" means.

Stateless:   Every DJ turn is built fresh: this module's own system prompt plus
             a per-turn message built from the ledger. It never reads or writes
             HomePanel's chat history, in either direction.

             Out, because gemma4:e2b imitates its own transcript (the reason
             that history keeps real tool exchanges at all — see
             home_panel.py's *History*). HomePanel's window is three turns, so
             a few songs of patter would evict the operator's actual
             conversation AND teach chat Pythia to talk like a radio host.
             In, because a DJ turn that could see the chat would start
             answering it.

Queue:       Songs are chosen BEFORE they are needed — four deep, topped up one
             at a time while a song plays — and a song ending is then a pop,
             not a turn.

             Because the picking was the gap. Measured 4.2s typically and 17.1s
             at worst, and every second of it was silence the listener sat
             through, for the simple reason that nothing knew what came next
             until the current song was already over. Moved under the music it
             costs nothing, and the same work buys the same answer.

             Nothing about WHO DECIDES changes. Entries pass the same guards on
             the way in, and pass them again at the pop, because an entry can be
             minutes old by the time it plays and two things move underneath it:
             the listener may have played that song themselves, and euterpe's
             config is re-read on every call. The pop's checks are LOCAL — no
             network — or the gap would come straight back.

             The framing to hold on to: the queue is an OPTIMISATION whose empty
             and invalid states are the pre-queue code path. An empty queue
             picks now, exactly as before; a queue where everything has gone
             stale drops the lot and picks now, exactly as before. Nothing new
             has to work for the DJ to keep working.

             Two bounds on the top-up, both load-bearing. It runs only while
             something is AUDIBLE, so a dashboard left on overnight with nothing
             playing does not spend the model all night for a queue nobody is
             draining. And it runs at most every _FILL_COOLDOWN, because a
             search that keeps failing plus a short queue is otherwise a yt-dlp
             process every 2s for the length of every song.

             And the breaker's meaning is PROTECTED rather than extended: a
             top-up that queues nothing counts no failure, because during a
             top-up a song is playing and nothing the listener can hear has
             failed. Counting there would switch Auto-DJ off, out loud, in the
             middle of a perfectly good song. The count happens in fetch(), the
             one place that knows a song did not start.

Trigger:     Latched intent, never a property read. _want_pick is set when a
             confirmed song ends, and honoured on a later tick once everything
             is quiet — so "wait for the chat to finish" costs nothing but a
             gap, and there is no timer to miss.

             The latch is a DEBT, and since 2026-09-18 both halves of that are
             written down. It is re-armed by an attempt that produced no music
             (_owe_a_pick), because a pick that was never made is still owed.
             It is discharged the moment anything becomes audible, whoever
             started it (_note_playing), because an owed pick means SILENCE
             that needs filling. Only clearing it on the way into a turn is
             what took Auto-DJ off the air on 2026-09-16 — see _owe_a_pick —
             and only re-arming it would have the DJ talking over the song the
             listener reached for while it was thinking.

             The end of a song CANNOT be read off mpv. Measured 2026-09-16: a
             natural end, a skip and a failed load leave byte-identical
             properties, and only a deliberate stop differs (playlist-count 0).
             So this watches for media that EXISTED and then stopped existing,
             which is intent tracked in Python — the same rule
             morpheus._judge_pending_load lives by, and CONVENTIONS §12 records.

Guards:      Run in CODE, at the moment a pick would play, never in the prompt.
             Measured over ten real turns: the model proposed a song already on
             the do-not-repeat list it had just been given six times. The list
             is a hint; the guard is the rule.

             They cannot live inside play_music, because that tool searches and
             plays in one call and these checks belong between those steps —
             and they are the DJ's preconditions, not Morpheus's job (§0). So
             the DJ supplies its own handler for the turn, behind Morpheus's own
             schema, via pythia.ask(tool_overrides=...).

             "Is this a song?" is NOT asked here — euterpe.is_a_song() owns it,
             so the ledger and this guard cannot disagree about what a song is.
             This module only adds the no-repeat check, which is the one thing
             that depends on the DJ rather than on the track.

             TWO PASSES, split by what each costs. The first reads fields the
             search already returned and is free. The second pays for a full
             yt-dlp extraction per candidate (morpheus.describe) and therefore
             runs only on what survived the first, and only _DEEP_CHECK_MAX
             times.

             The second pass exists because a title and a duration could not
             catch the chainsaw. A video about sharpening a chainsaw chain hits
             none of the fifteen denylist phrases and four minutes sits inside
             the 90-900s band, so it passed every check there was, played in
             full, and was announced as Linkin Park. The denylist could never
             have caught it either — a denylist can only ban what has already
             burned somebody, and the next offender is about something else.

             It both REJECTS and SELECTS, and which happens depends on what the
             metadata says rather than on a preference:

               evidence of something ELSE -> rejected. YouTube files it as
                   instructional AND nothing in its metadata says music. Both
                   halves required: a real track miscategorised is still a
                   track.
               evidence of MUSIC          -> selected, and the walk stops.
               no evidence either way     -> kept as the fallback and judged by
                   the old rules alone. Live sets, covers and indie uploads
                   routinely carry none, and a hard gate would starve the queue
                   of exactly the music worth finding.

             A describe() that fails is no evidence, so a yt-dlp outage degrades
             to the rules that existed before this pass did rather than taking
             Auto-DJ off the air.

             Affordable ONLY because of the queue: seconds per candidate is
             unthinkable in a live gap and free during a top-up. That is why it
             landed after the queue and not before.

             And the guards SELECT rather than reject: the pick walks
             _SEARCH_DEPTH results and takes the first that passes everything,
             instead of judging the top hit and giving the turn back to the
             model as an error to burn a round on.

Tools:       play_music ONLY. No control_music: this fires unattended and has no
             business pausing, stopping, replaying or changing the volume —
             those stay with Matthew and with chat Pythia. Enforced at DISPATCH
             (pythia._dispatch), not by omitting a schema, because this module
             feeds YouTube titles into a small model's context with nobody
             watching.

Logging:     This module writes NOTHING at INFO, and that silence is the point
             rather than an oversight.

             CONVENTIONS §5: Emanon folds levels into a health verdict, and
             routine work belongs at DEBUG because "it would drown Emanon at
             INFO". Emanon shows the last 40 lines (emanon._TAIL_LINES), and
             this job runs unattended for hours — roughly three lines a song at
             INFO would flush every real failure out of the one place a human
             looks, which is precisely the surface Auto-DJ most needs when it
             misbehaves overnight.

             Note what this costs, since 2026-09-17: the DJ's own picks are no
             longer written anywhere that outlives the process (see
             _session_plays), so "what did it play last night?" now has no
             answer beyond the DEBUG line in the log. That is the deliberate
             price of the ledger being the listener's LIST rather than a log of
             everything that played — a song worth keeping gets typed into the
             TUNES tab, which is the same act as choosing it. The DEBUG level
             still stands on the Emanon argument above, which never depended on
             the ledger.

             Three exceptions, all deliberate: the breaker trips at WARNING
             ("expected, recoverable" — Auto-DJ giving up is the one thing worth
             a verdict), a failed tick logs at ERROR with a traceback, and an
             attribution this module cannot PROVE logs at WARNING. A failed LOAD
             is logged at DEBUG here because Morpheus already owns that ERROR
             line; reporting it twice would double-count one fault.

             The third one is new on 2026-09-20 and it is the exception the
             argument above nearly cost us. Whose song is playing was decided in
             silence, at DEBUG, and the branch that filed a song as the
             LISTENER'S had no line at all — so when the DJ began filing its own
             picks as the listener's taste, the log could not say which rows
             were whose, or even that the question had been asked. It is
             "expected, recoverable" by §5's definition: expected occasionally
             (a local file, a playlist entry, an mpv too old to answer `path`),
             recoverable (the song is filed on the safe side), and rare by
             construction. That rarity is what keeps it Emanon-safe — and if it
             ever stops being rare, that is a real fault and Emanon SHOULD be
             shouting about it.

             The routine per-song lines stay at DEBUG, and the Emanon argument
             above is why. They do now EXIST on both branches, carrying the
             evidence behind the verdict, so the next forensic question about
             the ledger's provenance is answerable rather than guessed at.

Breaker:     Three turns in a row that end with no song playing and Auto-DJ
             turns itself off. Precedent: on 2026-08-20 a stale yt-dlp made
             every Morpheus play a silent no-op, and a DJ would have spun on
             that all night. It counts TURNS, not guard hits — guard hits are
             normal (eleven across ten measured turns, all of which still
             played).

             Until 2026-09-18 it could not reach three, and the sentence above
             was a description of nothing. Three ways, all fixed together
             because any one left standing keeps the other two unreachable:

               - the latch was not re-armed after a failed turn, so a second
                 turn never came (_owe_a_pick);
               - the DJ's OWN pick failing to load was not counted at all — by
                 then _playing_title is None and _observe returned before it
                 looked at the error slug (see "a pick we fired" there);
               - the count was cleared when play() was CALLED rather than when
                 a song became audible, so the stale-yt-dlp shape oscillated
                 0-1-0-1 for ever (_note_playing).

             "No song playing" means AUDIBLE. A play() call is a request.

             The held introduction rides the queue entry, which is the same
             invariant one stage earlier: the sentence lives on the record for
             the pick it describes, so dropping that pick anywhere takes the
             sentence with it, for free. Only the lifetime grows, from seconds
             to minutes.

Taper:       The seam between two songs used to be a cut. With a queue there is
             finally something to fade TO, so the DJ dips the outgoing song,
             swaps under the floor, and brings the new one up with its
             introduction over the top — which is what radio sounds like, and
             Harmonia ducks mpv for narration anyway.

             Morpheus owns the ramp, because Morpheus owns the player (§0).
             This module only says WHEN.

             Two triggers, and they are not equally risky:

               at the POP — no prediction at all. The dip is driven by an event
                 we caused, the down-ramp lands on an idle player, the hold
                 covers yt-dlp resolving, and the up-ramp catches the opening
                 bars. This is most of the polish for none of the risk.

               before the END (_maybe_taper) — reads `dur - pos`, which is the
                 one place this module trusts an mpv property for anything. It
                 is ADVISORY: it arms a gain and nothing else, sets no latch and
                 clears nothing, so §12's rule that a track ENDING is decided in
                 Python is untouched. Every way of being wrong is cheap, because
                 the dip is bounded and expires by itself. If any of this is
                 ever cut, cut that one.

             The two fire together on a handover and morpheus.taper() ignores
             the second, so one dip spans the whole thing rather than two
             fighting over it.

             The hold used to be TIMED — a fixed 6s racing a load neither
             trigger can see land. Measured against a real handover: yt-dlp
             resolving a queued pick takes roughly 4.5-6.5s, and _TAPER_LEAD
             (4.0) races a snapshot up to 2s stale, so the true remaining time
             at arming is anywhere in (0, 4]. Racing an 11s budget (2.5 down +
             6 hold + 2.5 up) against that pair meant the up-ramp routinely
             finished — volume back at 100 — before the song was audible, so
             the handover the listener actually heard was silence, then both
             the new song and the intro starting at once, at full volume. No
             amount of retuning the hold fixes a hold that cannot see the thing
             it is waiting for; it can only move which loads happen to fit.

             Since 2026-09-19 the hold is CONDITIONAL: morpheus.taper() arms a
             generous backstop (_TAPER_HOLD_MAX) rather than a real duration,
             and morpheus.release_taper() — called from _note_playing the
             instant anything becomes audible — is what actually ends it and
             starts the up-ramp, however long the wait actually took. The
             backstop is only for a load that never lands; bounding it is what
             stops that case holding the floor forever (see release_taper()'s
             own docstring for why a generous bound is the safe direction to
             err in). Tied to the SAME event that releases the held
             introduction (see "Announcing"), so the new song rises exactly as
             the DJ starts speaking, rather than the two landing on top of each
             other. The gain this produces is mpv's volume, not a per-track
             one, so a song that starts mid-hold — a retry after a failed
             load, say — simply inherits whatever the ramp has reached; nothing
             re-dips for it.

Upstream:    kairos.py (calls fetch), panels/vox_controls.py (the toggle),
             panels/home_panel.py (the rider that speaks the payload)
Downstream:  pythia.py (the turn), tools/morpheus.py (search + play),
             tools/euterpe.py (history), harmonia.py (is anything audible)

Requires:    logging, threading, time (stdlib); pythia, harmonia, and the two
             tool modules above. No new pip dependencies.
"""

import logging
import threading
import time
from typing import Any, Callable, Optional

import harmonia
import pythia
from tools import euterpe, morpheus

log = logging.getLogger("METIS.awen")

# ── The prompt ────────────────────────────────────────────────────────────────
# Lives here, not in machine_spirit.py: that module's job is PYTHIA's prompt,
# the one the operator edits and every chat answer uses. A background job's
# voice is not an edit to that.
#
# DO NOT TRIM THIS. Measured 2026-09-16 against gemma4:e2b, 12 turns each way:
# the full text calls play_music 12/12; a version three sentences shorter calls
# it 1/12, and the other eleven TYPE the call as prose —
# 'play_music{query:<|"|>Led Zeppelin Stairway to Heaven<|"|>}' — which is text
# a DJ would otherwise read aloud as an intro for a song that is not playing.
# Streaming was ruled out first (12/12 either way). The length is not padding;
# it is what keeps the model in tool-calling mode.
SYSTEM_PROMPT = (
    "You are Pythia, Felhaven's resident AI, working as its radio host. When a "
    "song ends, choose the next one and call play_music with the artist and song "
    "title. Match the listener's taste: related artists, the same genres, similar "
    "energy, but vary the artist. Never choose a song from the recently played "
    "list. If play_music returns an error, choose a different song. Once it is "
    "playing, introduce it in one or two short sentences, using the song and "
    "artist the tool returned. Do not make claims about a song's history or an "
    "artist's life. Speak in plain sentences with no Markdown, because your words "
    "are read aloud."
)

# The one tool this job gets. Enforced at dispatch — see "Tools".
_ALLOWED_TOOLS = ("play_music",)

# How many search results a pick considers. ONE was the original, and it made
# every guard a rejection: the model asked for a song, YouTube's top hit was a
# gear manual, and the whole turn came back as an error for the model to burn a
# round on. Looking further down the same result set turns that rejection into a
# SELECTION and costs nothing — it is the same yt-dlp call with a larger limit.
# Five rather than ten because a result the top-five cannot satisfy is usually a
# query problem, and the model re-picking is the better answer than this module
# digging.
_SEARCH_DEPTH = 5

# How many of those survivors get a FULL yt-dlp extraction (morpheus.describe).
# Each one is its own subprocess and its own second or three, and the budget is
# what stops a bad query costing five of them.
#
# Three rather than five because the first hit for "Artist - Song" usually IS
# the track, so the common case spends one; and because a query whose top three
# are all instructional videos is a query problem, and the model re-picking is a
# better answer than this module digging further down a bad list.
#
# Affordable only because of the queue. A full extraction inside a live gap
# would be seconds of silence added to the thing the queue exists to remove —
# which is why this landed second and not first.
_DEEP_CHECK_MAX = 3

# Every refusal says this, because the model needs telling what to do next.
# Measured: after any guard error it degrades to a bare artist name, whose top
# YouTube hit is a greatest-hits compilation that then trips the length cap.
_PICK_ADVICE = "Choose a specific song, giving an artist AND a song title."

# Three turns in a row with nothing playing and the flag goes off (see
# "Breaker"). Turns, not guard hits.
_MAX_CONSECUTIVE_FAILURES = 3

# Human wording for each is_a_song/guard slug, for the detail the model reads.
_REFUSAL_REASON = {
    "recently_played": "already played recently",
    "too_short":       "too short to be a song",
    "too_long":        "too long to be a song",
    "no_duration":     "a live stream, not a song",
    "bad_duration":    "not a song",
    "not_music":       "not music — it is a talk, review or manual",
    # FIXED wording. The category itself is network text and never interpolated
    # into a slug the model reads (§2).
    "not_music_category": "not music — YouTube files it under something else",
    "no_results":      "nothing usable",
}

_BREAKER_LINE = "Auto-DJ is off, I couldn't get a song to play."
_NO_HISTORY_LINE = "Play me something first so I know the vibe."

# Text that is a tool call the model TYPED instead of emitting. An intro is read
# aloud, so this must never reach Calliope; the code-built line replaces it.
_LEAK_MARKERS = ("play_music", "{query", "tool_call", "<|")

# ── State ─────────────────────────────────────────────────────────────────────
# Touched by the Kairos worker thread and by the UI thread (the toggle), so the
# compound reads-then-writes take _lock. §6 reserves cross-thread sharing for
# the result queue; this is the allowance calliope's auto-speak flag already
# has, widened to a lock because the flag here is read together with other
# fields rather than on its own.
_lock = threading.Lock()

# OFF at every launch, and deliberately not persisted (CONVENTIONS §12). A job
# that starts playing music by itself because of something you did last week is
# not a feature.
_enabled: bool = False

# The RAW mpv title of the song currently confirmed playing, or None. This is
# the whole trigger: a title that was set and is now gone is a song that ended.
#
# Raw, not normalized, and that is a scar. It held the normalized KEY until
# 2026-09-16, and the key depends on the channel — which is known only on the
# tick that consumes _pending_pick. The tick after, the channel was "", the key
# came out different, the song looked new, and it was recorded a SECOND time as
# a `user` pick. Eight of them reached a real ledger, and because the taste
# anchor reads `user` rows, every one of those DJ choices became "what Matthew
# likes". One bad pick then fed the next. The raw title is the same on every
# tick, so identity does not depend on what we happen to know about the song.
_playing_title: Optional[str] = None

# Latched intent — "a pick is owed". Set when a song ends (or when the flag is
# switched on with nothing playing), honoured on a later tick once everything
# is quiet.
_want_pick: bool = False

# What the DJ last played, so the observer can label the next song it sees as
# `dj` rather than `user`. Cleared once matched, or when anything else happens.
#
# It is also the DJ's "is my pick still in flight?" flag, which is what lets a
# pick that never loads be counted and retried rather than silently ending the
# night (_observe, "a pick we fired"). That reader adds one key of its own,
# "clean" — set once a snapshot arrives with no error slug on it, which is
# Morpheus's cache confirming it has caught up with our play(). Nothing else
# reads it, and _note_playing matches on "key" alone.
_pending_pick: Optional[dict[str, Any]] = None

# The DJ's OWN picks this run, as minimal ledger-shaped rows. This is the whole
# of its "don't replay myself" memory, and it is deliberately in memory only.
#
# Until 2026-09-17 these went into euterpe_ledger.json as `dj` rows, which is
# why they are shaped like ledger rows here — euterpe's views take a plays=
# argument, so feeding them this list costs no new matching rule that could
# drift from was_recently_played(). They carry source "dj" so taste_anchor()
# skips them by the rule it already has.
#
# Why not the ledger any more: with an editor on it, that file is the listener's
# LIST — a taste reference they curate — and a job that writes to the list it
# reads from is a feedback loop wearing a different hat. Why not a file of its
# own: the window is 20 songs, about an evening, and Auto-DJ is already off at
# every launch and not persisted (§12). The cost is stated rather than hidden —
# a restart may let it replay something from earlier the same day.
_session_plays: list[dict[str, Any]] = []

# ── The queue ─────────────────────────────────────────────────────────────────
# Picks that have passed every guard and are waiting their turn, oldest first;
# _queue[0] is the next song to play.
#
# The whole point of it. Picking measured 4.2s typically and 17.1s at worst, and
# every second of that was SILENCE the listener sat through, because nothing
# knew what came next until the current song was already over. Filled while a
# song plays, it costs nothing and the gap disappears.
#
# An entry is a pick that has not been FIRED yet, and is deliberately the same
# shape _pending_pick already has, so the pop is a copy rather than a
# translation:
#   {"key", "title", "channel", "url", "duration", "intro"}
#
# In memory, this run only — the _session_plays argument unchanged (§12): the
# flag is off at every launch, so a queue that survived a restart would
# resurrect picks made against a taste that has since moved.
#
# KEPT across an off/on cycle, which is the mirror of set_enabled()'s note that
# off-then-on is not a request to start repeating songs. It is not a request to
# throw away picks already made either, and staleness is handled at the pop.
_queue: list[dict[str, Any]] = []

# Top up while below this. Four is the listener's own figure, and it is about an
# evening's worth of lookahead without banking so many that a drifting taste
# takes twenty minutes to correct.
_QUEUE_TARGET = 4

# The handler refuses beyond this. One deeper than the target so a turn that
# lands two picks is not punished for it.
_QUEUE_MAX = 5

# Seconds between top-up turns. NOT optional: without it, a failing search and a
# short queue means a yt-dlp call every 2s for the length of every song, all
# evening. This is the one genuinely unbounded behaviour the queue introduces
# and it needs an explicit bound. A doubling backoff is the refinement if a flat
# 20s proves too eager; it is not what ships first.
_FILL_COOLDOWN = 20.0

# When the last top-up turn started. Worker thread only.
_last_fill_at: float = 0.0

# How many seconds before a song's end the handover dip starts. Sized against
# the snapshot's 2s staleness rather than against the music: at 4s the trigger
# tick sees a true remaining time in (0, 4], so the dip always lands inside the
# song rather than after it.
_TAPER_LEAD = 4.0

# The raw title we have already tapered for, so one song cannot dip twice.
_tapered_title: Optional[str] = None

# A serial per queue entry, so the UI can name one without using its index.
# The worker pops from the HEAD while a box is open, so index n is a different
# song by the time a click lands. Monotonic and never reused, which also makes
# it immune to the ABA the URL would have: an entry removed and an identical
# one queued a minute later are different entries, and saying so costs nothing.
_next_entry_id: int = 0

# Bumped on every append, pop and removal. The UI watches this instead of
# rebuilding a snapshot sixteen times a second: one int against one int, and
# only a change pays for queue_view().
_queue_revision: int = 0

# Songs taken OFF the queue by hand, as minimal ledger-shaped rows.
#
# Without it, removing a song does not ban it: _known_plays() is what the guard
# and the model's do-not-choose list read, so the moment an entry leaves the
# queue it also leaves both — and the next top-up, under the same taste and
# within _FILL_COOLDOWN, can queue the very song just rejected. To anyone
# watching, that is the button not working.
#
# Shaped and sourced exactly like _session_plays (§ "Reads, does not write"),
# so taste_anchor() skips it by the rule it already has and no new matching
# rule can drift from was_recently_played(). In memory, this run only.
_declined: list[dict[str, Any]] = []
_DECLINED_CAP = 50

# How many session picks to keep. Not the no-repeat window, which is config and
# applied at READ time (§9 — derive, don't store): this only stops an app left
# running for a week from growing a list without end.
_SESSION_CAP = 200

# The normalized key of the last song observed playing, WHOEVER started it.
# Deliberately not cleared when that song ends: it is read at pick time, which
# is after the end, and "just finished" is the one fact a turn message cannot
# get from either list — the ledger no longer holds the DJ's picks, and the
# session list does not hold the listener's.
_last_played_key: Optional[str] = None

# Consecutive turns that ended with nothing playing (see "Breaker").
_failures: int = 0

# True from the moment a turn starts until it returns. Read by the UI at 16 Hz
# to label the gap (see status_line), so it is written on the worker thread and
# read on the main one — the is_warming()/is_audible() shape, a plain bool that
# needs no lock because nothing branches on it twice.
_picking: bool = False


# ── The flag ──────────────────────────────────────────────────────────────────

def enabled() -> bool:
    """Is Auto-DJ on? The single source of truth — the toggle reads this every
    refresh and never keeps a copy (the calliope.auto_speak_enabled()
    discipline)."""
    return _enabled


def set_enabled(value: bool) -> Optional[str]:
    """Turn Auto-DJ on or off. Returns a line to say, or None.

    Switching ON with nothing playing arms a pick immediately — waiting for a
    song to end when none is playing would mean waiting forever. With no taste
    to match it says so instead of guessing.

    Switching OFF disarms the pending pick and nothing else. Whatever is playing
    plays out; this stops the NEXT one, it does not stop the music. It also
    leaves _session_plays alone on purpose: off-then-on is not a request to
    start repeating the songs it just played.
    """
    global _enabled, _want_pick, _failures
    with _lock:
        _enabled = bool(value)
        if not _enabled:
            _want_pick = False
            return None
        _failures = 0          # a deliberate switch-on resets the breaker
        if _playing_title is not None:
            return None        # something is on; pick when it finishes
    # Outside the lock: this reads the ledger file.
    #
    # The ANCHOR, not entries(): a ledger is only useful here if something in it
    # is the listener's own. A file holding nothing but hand-typed `dj` rows —
    # "these played, but they are not my taste" — is not empty and has no vibe
    # in it either, and guessing from it is the drift this job already has.
    if not euterpe.taste_anchor():
        log.debug("Awen: enabled with no taste anchor — nothing to match yet")
        return _NO_HISTORY_LINE
    with _lock:
        _want_pick = True
    return None


def toggle() -> Optional[str]:
    """Flip the flag. Returns whatever set_enabled() wants said."""
    return set_enabled(not _enabled)


def status_line() -> str:
    """What the UI should show about Auto-DJ right now, or "" for nothing.

    A FREE read — no IPC, no lock, no ledger — because HomePanel calls it from
    its 60 ms drain chain, the same chain morpheus.is_audible() is written for.

    len(_queue) without the lock needs its own sentence, because the allowance
    written here was for _picking, "a plain bool that needs no lock because
    nothing branches on it twice", and a list length is not a plain bool. It is
    still safe for a different reason: len() on a list is one atomic size read
    under the GIL, and every write to _queue is under _lock, so the worst an
    unlocked reader gets is a count that is STALE, never one that is wrong. The
    staleness budget is one frame — _tick_status repaints on transition at
    16 Hz, so a count one pop behind lives 60 ms and corrects itself.

    That allowance covers len() and NOTHING else. _queue[0] from here is a real
    IndexError against a concurrent pop, and iterating it is a real "list
    changed size during iteration". Which is exactly why queue_view() takes the
    lock and this does not.

    It exists because a DJ turn is a SILENT GAP. Picking measured 4.2s typically
    and 17.1s at worst (each guard re-pick is a real yt-dlp search), and until
    now the screen said nothing at all for that whole stretch — the song just
    stopped and eventually something else started. Two states are worth naming:

      - picking: the turn is in flight. This is the long one.
      - owed but not quiet: a pick is waiting for narration or a chat answer to
        finish. Without this the wait looks identical to Auto-DJ being broken,
        which is exactly the wrong conclusion to invite.

    It used to say nothing while merely enabled and mid-song, on the grounds
    that the toggle's amber lamp already carries "Auto-DJ is on". The queue
    count REVERSES that, deliberately: the lamp does not say how many songs are
    lined up, and since 2026-09-19 this label is also a CONTROL — clicking it
    opens the queue — and an affordance nobody can see is not an affordance.

    Priority, and it is not arbitrary: the first two lines explain a SILENCE the
    listener is sitting through, so they outrank a count that is merely ambient.
    A queue is only interesting when nothing is wrong.
    """
    if _picking:
        return "Auto-DJ: choosing the next song…"
    if _enabled and _want_pick:
        return "Auto-DJ: waiting for quiet…"
    if _enabled and _queue:
        return f"Auto-DJ: {len(_queue)} queued"
    return ""


# ── The DJ's play_music ───────────────────────────────────────────────────────

def _guarded_play_music(query: str = "") -> dict[str, Any]:
    """play_music for a DJ turn: search, CHECK, then play.

    Morpheus's own handler searches and plays in one call, which is right for
    "play me this song" and wrong here — by the time it returns, a song already
    heard an hour ago is already playing. These checks also do not belong to
    Morpheus: they are about this job's history and this job's idea of a song.
    So the DJ brings its own body and borrows Morpheus's schema
    (pythia.ask(tool_overrides=...)).

    Every refusal is a §2 slug plus a detail written FOR the model, so it can
    re-pick inside the same turn. The details are not decoration — measured over
    ten real turns, after any guard error the model degrades to a bare artist
    name, whose top YouTube hit is a greatest-hits compilation that then trips
    too_long. Saying "an artist AND a song title" is aimed squarely at that.

    Never raises: it runs inside pythia's dispatcher, which would turn an
    exception into a tool_failed slug anyway, but a guard that crashes is a
    guard that stopped guarding.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "no_query",
                "detail": "Name an artist and a song title."}

    with _lock:
        full = len(_queue) >= _QUEUE_MAX
    if full:
        return {"error": "queue_full",
                "detail": "There are already enough songs lined up."}

    rows = morpheus.search(query, limit=_SEARCH_DEPTH)
    if not rows or "error" in rows[0]:
        return {"error": "no_results",
                "detail": f"Nothing found for {query!r}. {_PICK_ADVICE}"}

    # Read once, outside the loop: five results would otherwise re-read the
    # ledger file five times to answer the same question (the plutus.positions
    # idiom, and the same fold _turn_message does).
    known = _known_plays()

    # TWO PASSES, and the split is about what each one costs.
    #
    # The first is free: it reads fields the search already returned. The second
    # pays for a full yt-dlp extraction per candidate, so it runs only on what
    # survived the first and only _DEEP_CHECK_MAX times.
    refused: "Optional[tuple[str, str]]" = None
    survivors: list[dict[str, Any]] = []
    for row in rows:
        title = str(row.get("title") or "")
        channel = str(row.get("channel") or "")
        url = str(row.get("url") or "")
        if not url or not title:
            continue

        # Against BOTH lists (see _known_plays). The ledger alone would leave
        # the DJ blind to its own picks, including the song that just ended, and
        # the prompt does not cover for that: measured over ten real turns, the
        # model proposed a song already on the list it had just been handed six
        # times. The list is a hint; this is the rule.
        if euterpe.was_recently_played(title, url, channel, plays=known):
            refused = refused or ("recently_played", title)
            continue

        # The SAME judgment the ledger applies to a prior (euterpe.is_a_song),
        # so the DJ can never play something the ledger would refuse to
        # remember, or be refused something the ledger would happily store.
        why = euterpe.is_a_song(title, row.get("duration"))
        if why is not None:
            refused = refused or (why, title)
            continue
        survivors.append(row)

    # The deep pass. It both REJECTS and SELECTS, and which one happens depends
    # on what the metadata actually says:
    #
    #   positive evidence of something ELSE  -> rejected outright. YouTube files
    #       it as instructional and nothing in its metadata says music. This is
    #       the chainsaw, and it is the half the guards did not have before.
    #   positive evidence of MUSIC           -> selected, and the walk stops.
    #   no evidence either way               -> kept as the fallback, judged by
    #       today's rules alone. Live sets, covers and indie uploads routinely
    #       carry no metadata, and a hard gate would starve the queue of exactly
    #       the music worth finding.
    #
    # A describe() that fails is no evidence, so an outage degrades to the rules
    # that existed before this pass did, rather than taking the DJ off the air.
    # The details are KEPT this time. describe() has already been paid for, and
    # its artist/track pair is the only place in the stack that says which half
    # of a title is which — euterpe.has_music_evidence() reads the same two
    # fields to decide the pick and then throws the values away.
    chosen: Optional[dict[str, Any]] = None
    chosen_details: dict[str, Any] = {}
    fallback: Optional[dict[str, Any]] = None
    fallback_details: dict[str, Any] = {}
    for row in survivors[:_DEEP_CHECK_MAX]:
        title = str(row.get("title") or "")
        details = morpheus.describe(str(row.get("url") or ""))
        why = euterpe.is_a_song(title, row.get("duration"), details)
        if why is not None:
            refused = refused or (why, title)
            continue
        if fallback is None:
            fallback, fallback_details = row, details
        if euterpe.has_music_evidence(details):
            chosen, chosen_details = row, details
            break
    if chosen is None:
        chosen, chosen_details = fallback, fallback_details

    if chosen is None:
        # ONE slug, with the specific reason in the human half. A slug picked
        # from a variable is not verifiable by
        # tests/test_tool_output_contract.py — it parses the AST and cannot
        # resolve a name — and §2 wants the slug BEFORE any interpolation so it
        # stays greppable. It also reads better to the model: whichever guard
        # refused, the action is the same, and the reason is right there in
        # words it can act on.
        slug, offender = refused or ("no_results", query)
        why = _REFUSAL_REASON.get(slug, slug)
        return {"error": f"no_usable_result: nothing found for {query!r} will "
                         f"do — {offender!r} is {why}. {_PICK_ADVICE}"}

    title = str(chosen.get("title") or "")
    channel = str(chosen.get("channel") or "")
    url = str(chosen.get("url") or "")
    row = chosen

    # QUEUED, not played. morpheus.play() has moved to the pop
    # (_play_from_queue), which is what lets this run while a song is still
    # going and removes the gap entirely.
    #
    # The intro is built HERE, and _take_a_turn overwrites it if the model's own
    # line passes every check. Built first because the model may call this twice
    # in one turn and only one line comes back: every entry then carries a true,
    # speakable sentence, and there is no entry that can reach the speakers with
    # nothing to say.
    #
    # artist/track come straight from yt-dlp and are "" when it had none — a
    # failed describe() is {"error": ...}, whose .get("artist") is None, so the
    # absent case needs no guard of its own. queue_view() decides what to do
    # with them; storing them raw keeps the judgment in one place.
    global _next_entry_id, _queue_revision
    with _lock:
        _next_entry_id += 1
        entry = {"id":       _next_entry_id,
                 "key":      euterpe.normalize(title, channel),
                 "title":    title,
                 "channel":  channel,
                 "url":      url,
                 "duration": row.get("duration"),
                 "artist":   str(chosen_details.get("artist") or ""),
                 "track":    str(chosen_details.get("track") or ""),
                 "intro":    f"Now playing: {euterpe.pretty(title, channel)}."}
        _queue.append(entry)
        _queue_revision += 1
        waiting = len(_queue)
    log.debug("Awen: queued %r (%d waiting)", title, waiting)
    # The tool result is unchanged, and "now_playing" is now a small lie about
    # TIMING. Deliberate: the model's turn stays identical in every observable
    # way — same schema, same keys, same prompt — rather than re-opening a
    # prompt whose tool-calling behaviour was measured at 12/12.
    return {"now_playing": title, "channel": channel, "url": url}


# ── Observation ───────────────────────────────────────────────────────────────

def _observe(snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Record what is playing, and notice when it stops.

    Returns a payload for the rider, or None for "nothing to announce". Two
    things come back this way, and both are things only the observer can know:
    the breaker giving up, and a song's introduction, which is held until the
    song is audible (see "Announcing").

    The breaker line used to be dropped here — _count_failure()'s result was
    discarded, so tripping from the OBSERVER turned the lamp off in silence
    while tripping from a turn announced itself. The same event is not worth
    two different amounts of explanation.

    Called every tick with Morpheus's CACHED snapshot — a free read, never an
    IPC round trip, so this cannot become a second poller confirming and
    condemning loads on its own schedule (morpheus.last_snapshot()).

    Records whether or not Auto-DJ is on. Otherwise the first time it is
    switched on there would be no priors at all, and the whole point of the
    taste anchor is that it is made of songs Matthew chose.
    """
    global _playing_title, _want_pick, _pending_pick, _failures

    title = snapshot.get("title")
    has_media = snapshot.get("pos") is not None or snapshot.get("dur") is not None

    if snapshot.get("running") and has_media and title:
        intro = _note_playing(str(title), snapshot)
        _maybe_taper(snapshot, str(title))
        return _spoken(intro, "intro", now_playing=str(title))

    if not snapshot.get("running"):
        # No PLAYER at all, which is not the same thing as a song ending — and
        # the difference is not academic. Arming a pick here would call play(),
        # and play() STARTS mpv. felhaven._on_close runs morpheus.shutdown()
        # BEFORE kairos.stop(), so a worker thread already inside fetch() at
        # close would spawn a fresh mpv.exe with nothing left to shut it down:
        # precisely the orphan that shutdown() call exists to prevent.
        #
        # It is also genuinely unknowable. mpv being gone could mean the app is
        # closing, or mpv crashed, or play() recycled it because its log grew
        # too large — none of which is "that track finished, play another".
        # Forget the song, arm nothing, and wait to be told otherwise.
        with _lock:
            _playing_title = None
            _pending_pick = None
        return None

    # A pick we fired that never became audible. Checked BEFORE the trigger
    # below, because by the time it fails _playing_title is already None — the
    # song whose ending armed the pick has ended — so `was is None` returns
    # first and drops this on the floor. It did, until 2026-09-18: the DJ's own
    # failed loads were neither counted nor retried, which is exactly the
    # 2026-08-20 stale-yt-dlp outage the breaker exists for. The test that
    # thought it covered this ticked a PLAYING snapshot first, which is a
    # LISTENER's load failing — the one shape this path never takes.
    # Only once a CLEAN snapshot has been seen since the pick, which is how we
    # know this verdict is about our load and not the last one. last_snapshot()
    # is Morpheus's cache and may be a tick stale, while play() clears the old
    # slug in Morpheus's own state — so the tick right after a pick can hold a
    # pick-shaped `_pending_pick` beside the PREVIOUS pick's sticky error. That
    # is not hypothetical: it is the retry path this fix adds, where the pick
    # before this one is exactly the one that failed. A clean snapshot is
    # Morpheus's cache confirming it has caught up.
    with _lock:
        stillborn = False
        if _pending_pick is not None:
            if not snapshot.get("error"):
                _pending_pick["clean"] = True
            elif _pending_pick.get("clean") and not snapshot.get("loading"):
                stillborn = True
                # Consumed here so it is counted ONCE. morpheus keeps
                # _load_error set until the next play() (_judge_pending_load),
                # so the slug rides every later snapshot; counting per tick
                # would trip the breaker in six seconds.
                _pending_pick = None
    if stillborn:
        # The held introduction goes with it. It described a song that never
        # played, which is the whole reason it was held rather than spoken.
        log.debug("Awen: our pick never loaded (%r)", snapshot.get("error"))
        notice = _count_failure()
        _owe_a_pick()
        return _spoken(notice, "notice")

    # mpv is up but has no media. Was something playing?
    with _lock:
        was = _playing_title
        if was is None:
            return None
        if snapshot.get("loading"):
            return None                 # between tracks, not the end of one
        _playing_title = None
        _pending_pick = None

    if snapshot.get("error"):
        # A failed load. Not an end, and it feeds the breaker rather than the
        # trigger — firing a fresh pick into a broken player is how a stale
        # yt-dlp spins all night.
        log.debug("Awen: the player reported %r — not treating that as a song "
                 "ending", snapshot.get("error"))
        # No _owe_a_pick() here, and the asymmetry with the stillborn path above
        # is the point: a song WAS playing, so this is the LISTENER's next track
        # failing to load, with them sitting right there watching it fail.
        # Jumping in with a song of our own is the DJ talking over a person.
        return _spoken(_count_failure(), "notice")

    if snapshot.get("pl_n") == 0:
        # A DELIBERATE stop, the one idle state a property separates. Silence is
        # what was asked for, so Auto-DJ stays on and simply waits for the next
        # song Matthew starts.
        log.debug("Awen: deliberate stop — staying quiet")
        return None

    # A natural end, or a skip. Measured identical, and they mean the same thing
    # here: that song is over, play another.
    log.debug("Awen: %r ended", was)
    with _lock:
        if _enabled:
            _want_pick = True
    return None


def _maybe_taper(snapshot: dict[str, Any], title: str) -> None:
    """Start the handover dip a few seconds before this song ends.

    ADVISORY, and that word is the whole design. §12 says whether a track ENDED
    is decided in Python and never read off mpv, and this does not touch that:
    it sets no latch, clears nothing, and returns nothing. _want_pick is still
    armed only by media that existed and then stopped existing. A gain is not a
    verdict.

    Which is what makes `dur - pos` safe to use here when it is not safe
    anywhere else. The snapshot is up to 2s stale and both fields are nullable,
    so the trigger tick sees a true remaining time somewhere in (0, _TAPER_LEAD]
    — and every way of being wrong is cheap:

      - too early: the song plays out its last second at the floor, under the
        DJ's voice, which is what a radio handover sounds like anyway;
      - too late: a shorter dip;
      - wrong entirely, because `dur` was a lie: ONE dip-and-swell over a song
        that keeps playing, because the taper is a bounded dip with a floor of
        0.15 and it expires on its own (morpheus.taper).

    Once per song, keyed on the RAW title — the same identity _note_playing uses
    and for the same reason. Keying on anything derived would make one song look
    like two a tick apart, which is the 2026-09-16 scar in a new costume, and
    here it would mean dip, swell, dip again.

    Never while the queue is empty: dipping before a gap we cannot fill is worse
    than a clean cut.
    """
    global _tapered_title
    if not _enabled:
        return
    if snapshot.get("paused") or snapshot.get("loading"):
        return
    pos, dur = snapshot.get("pos"), snapshot.get("dur")
    if pos is None or dur is None:
        return
    with _lock:
        if not _queue or _tapered_title == title:
            return
    # The same authority the guard and the ledger use, which rules out live
    # streams and the lore videos and full albums Morpheus is also used for —
    # a "remaining" figure means nothing on a stream that never ends.
    if euterpe.is_a_song(title, dur) is not None:
        return
    remaining = float(dur) - float(pos)
    if not 0.0 < remaining <= _TAPER_LEAD:
        return
    with _lock:
        _tapered_title = title
    log.debug("Awen: %.1fs left of %r — tapering", remaining, title)
    morpheus.taper()


def _whose_song(pending: Optional[dict[str, Any]],
                snapshot: dict[str, Any],
                key: str) -> str:
    """Who started the song that just became audible: "dj", "user" or
    "unproven".

    THREE answers, not two, and the third is the whole point. The old test was
    a fuzzy string comparison between two strings written by DIFFERENT
    PRODUCERS — pending["key"] from yt-dlp's search result, `key` from mpv's
    media-title — and it had no way to say "I cannot tell". It resolved every
    doubt towards "user", which is the expensive direction:

        a listener's song filed "dj"  -> one missed taste signal, this run only
        a DJ's song filed "user"      -> a permanent row in a curated file that
                                         taste_anchor() reads as what the
                                         listener likes, which then steers the
                                         next pick. That is the feedback loop,
                                         and it has now closed twice.

    Measured 2026-09-20 on the live ledger: mpv's media-title arrived in NFD
    where yt-dlp's search result was NFC — "Altın Gün" spelled with a combining
    diaeresis against the precomposed letter. One upload, one video id, and
    same_song() said no. euterpe.normalize() folds Unicode form now, so that
    particular divergence is gone; this function exists because the NEXT one
    will not be Unicode, and a comparison between two producers' spellings can
    always be made to disagree.

    So the exact signal is asked first. mpv's `path` is the URL that produced
    the audio now sounding, pending["url"] is the URL we asked for, and
    euterpe.video_id() reduces both to the same eleven characters — a fact
    rather than a resemblance.

    "unproven" is deliberately NOT in euterpe.SOURCES, so it cannot reach
    record() even by accident: record() would refuse it and log a WARNING.
    """
    if pending is None:
        # Nothing of ours is in flight, so there is no doubt to resolve. The
        # listener's own play, chat Pythia's play_music, MorpheusPanel — all
        # three are theirs. THIS is why the change cannot starve the taste
        # anchor: an ordinary listener play never reaches the clauses below.
        return "user"

    loaded = euterpe.video_id(str(snapshot.get("path") or ""))
    wanted = euterpe.video_id(str(pending.get("url") or ""))
    if loaded is not None and wanted is not None:
        # THE test, and the only one here that cannot be argued with.
        return "dj" if loaded == wanted else "user"

    # No id on one side. A local file, a playlist entry, an mpv too old to
    # answer `path`, or a pick whose search result carried no watchable URL —
    # and also a resolved stream URL, which video_id() correctly refuses
    # (measured: the signed googlevideo form has no eleven-character id in it).
    # Fall back to the comparison that was the whole test until today: right
    # most of the time, and wrong in exactly the way that brought us here.
    if euterpe.same_song(str(pending.get("key") or ""), key):
        return "dj"
    return "unproven"


def _note_playing(title: str, snapshot: dict[str, Any]) -> Optional[str]:
    """A song is audible. Note it the first time we see it.

    Returns the introduction held for this song, if it is ours and one was
    held — this is where a DJ pick stops being a request and becomes a fact,
    so it is the first moment anything true can be said about it. None
    otherwise, which covers every song the listener started themselves.

    WHOSE song it is decides where it goes, and the split is the point:

      the listener's  ->  euterpe.record(source="user"), the ledger on disk
      the DJ's own    ->  _session_plays, this process only

    A job that wrote to the list it reads from would be a feedback loop, and one
    already happened — see _playing_title. The ledger is the listener's taste
    now; the DJ only borrows it.

    Identity across ticks is the RAW TITLE, never the normalized key. The key
    depends on the channel, the channel is known only on the tick that consumes
    _pending_pick, and keying on it therefore made the SAME song look like two
    different ones a tick apart — noting it twice, the second time as a `user`
    pick. The key is still what gets stored; it just is not what decides "is
    this the song I already saw".

    Also where a running taper is released (morpheus.release_taper()) — on the
    "different song" branch only, never on the same-song return above it. A
    taper dips the OUTGOING song, which is already audible when it is armed, so
    releasing on "anything audible" rather than "a NEW thing is audible" would
    end the hold on the very song the dip was for. See awen.py's "Taper"
    section.
    """
    global _playing_title, _pending_pick, _last_played_key, _failures
    global _want_pick

    with _lock:
        # Anything audible discharges the debt, whoever started it: an owed pick
        # means SILENCE that needs filling, and this is not silence.
        #
        # Cleared BEFORE the same-song return below, and above every other line
        # here, because the latch can now be re-armed by an attempt that played
        # nothing (_owe_a_pick) — and the listener may well have started
        # something of their own during those 4-20 seconds, in which case this
        # song was noted on an earlier tick and every later one returns right
        # here. Without this the DJ talks over a song a person just chose, which
        # is the one thing an unprompted job must never do.
        _want_pick = False
        if title == _playing_title:
            return None                 # same song, already noted
        pending = _pending_pick
        _playing_title = title
        # A DIFFERENT song is now audible, whoever started it — end any
        # handover hold NOW rather than on morpheus's own backstop clock. See
        # morpheus.release_taper() and awen.py's "Taper" docstring section.
        morpheus.release_taper()
        # Ours if the pick we fired resolved to this title. The two keys are
        # NOT computed from the same string: the pending one came from the
        # search result's title, this one from mpv's media-title, and YouTube
        # ships the same song both ways round. A bare == therefore filed the
        # DJ's own pick as the listener's whenever those two spellings differed
        # — the 2026-09-16 feedback loop reached through a comparison instead of
        # a label. euterpe.same_song() is the one judgment that already knows
        # what "the same song" means; asking it here is what stops this
        # disagreeing with the guard and the ledger.
        channel = str((pending or {}).get("channel") or "")
        # Built WITH the pending channel because _whose_song's title fallback
        # has to compare like with like — pending["key"] carries that channel
        # too.
        key = euterpe.normalize(title, channel)
        whose = _whose_song(pending, snapshot, key)
        _pending_pick = None
        # Whoever started it — this is what "just finished" reads. Keyed to
        # match the list the row actually LANDS in: with the channel when we
        # keep the row ourselves, without it when record() will re-derive the
        # key from the title alone (it is called with channel=""). A key that
        # matches neither list is a "just finished" line naming a song nothing
        # has heard of — the 2026-09-16 channel-prefix asymmetry, one field
        # over.
        _last_played_key = (key if whose != "user"
                            else euterpe.normalize(title)) or _last_played_key

        if whose != "user":
            # "dj" AND "unproven" both land here, and that is the fix. A song we
            # cannot prove is not ours is filed where a wrong answer costs one
            # missed taste signal for one run, rather than a permanent row in
            # the very list this job reads to decide what to play next.
            _session_plays.append({
                "title":    title,
                "key":      key,
                # From what mpv says it loaded; the pick's own url only when we
                # PROVED they are the same song, or matched on title, in which
                # case it is the best id available.
                "video_id": (euterpe.video_id(str(snapshot.get("path") or ""))
                             or (euterpe.video_id((pending or {}).get("url"))
                                 if whose == "dj" else None)),
                # Shaped like a ledger row so euterpe's views can read it, and
                # sourced "dj" so taste_anchor() skips it by its existing rule.
                "source":   "dj",
            })
            del _session_plays[:-_SESSION_CAP]

            if whose == "dj":
                # THE breaker reset, and it lives here rather than at the end of
                # a turn because the breaker counts turns that end with "no song
                # PLAYING" and a play() call is not a song playing. Resetting on
                # the call is what let the 2026-08-20 stale-yt-dlp shape run all
                # night: pick, reset, fail, count, pick, reset — the count
                # oscillated 0-1-0-1 and three in a row never happened. Set
                # inline, not via a helper: _lock is held and it is not
                # reentrant.
                #
                # NOT reset on "unproven": the breaker means "three turns in a
                # row produced no song", and a song we cannot attribute is not
                # evidence that OURS played. A stale count self-corrects on the
                # next proven pick; a wrong reset keeps a dead DJ on the air.
                _failures = 0
                log.debug("Awen: %r is ours (%s) — session memory, not the "
                          "ledger", title,
                          f"video id {euterpe.video_id(str(snapshot.get('path') or ''))}"
                          if snapshot.get("path") else "by title")
                # Released from the record it was held on, so it cannot outlive
                # the pick it describes and cannot be read twice: the same-song
                # return above means every later tick for this song stops
                # before here.
                return str((pending or {}).get("intro") or "") or None

            # Unproven: filed on the safe side, and SILENT. The held line names
            # a specific song, and telling a listener they are hearing something
            # they are not is the expensive half (§12, 2026-09-17). No failure
            # counted and no re-arm either: something IS audible, and _want_pick
            # was cleared at the top of this function for exactly that reason —
            # the DJ does not talk over a person.
            #
            # WARNING rather than DEBUG, and it is the third exception to this
            # module's no-INFO rule (see "Logging"). It is the module failing to
            # establish a fact it needs, it is rare by construction, and if it
            # ever stops being rare that is a fault worth Emanon's verdict.
            log.warning("Awen: cannot prove who started %r (our pick was %r, "
                        "mpv path %r) — filed as the DJ's, saying nothing",
                        title, str((pending or {}).get("title") or ""),
                        snapshot.get("path"))
            return None

    # The listener's. The only path that writes to the file they curate.
    log.debug("Awen: %r is the listener's — recorded", title)
    euterpe.record(title,
                   # The URL mpv actually loaded, so a ledger row finally
                   # carries a video_id. Every row on the live ledger had
                   # "video_id": null, which left the id branch of
                   # was_recently_played() dead against the file it was written
                   # for. video_id() returns None for anything that is not a
                   # watchable YouTube URL, so a local file simply has none.
                   url=str(snapshot.get("path") or "") or None,
                   # Still "": mpv reports no channel, and describe() is a
                   # network call with no business on a Kairos tick.
                   channel="",
                   duration=snapshot.get("dur"),
                   source="user")
    return None


def _known_plays() -> list[dict[str, Any]]:
    """Everything that counts as "recently played": the listener's ledger plus
    the DJ's own picks this run.

    ONE list, built in one place, so the guard and the message the model reads
    cannot end up disagreeing about what has been played — a song the guard
    refuses but the message does not mention costs a whole turn to a refusal the
    model was never warned about.

    Concatenation is enough for every consumer: was_recently_played() is a
    membership test, and recent_titles() takes the tail and de-duplicates. The
    one thing order WOULD matter for is "just finished", which is why that reads
    _last_played_key instead of the tail of this.

    Songs taken off the queue by hand are in here too, and they belong on THIS
    side rather than in _played_rows(): they were never heard, so calling them
    played would make that function's own docstring a lie — but they were
    certainly chosen, and rejecting one has to mean the DJ stops offering it.

    Since the queue exists this also covers songs that have not played YET, and
    that is the same rule rather than a new one: a song already waiting in the
    queue must not be chosen again, and the model must be told so in the same
    breath the guard is told. Because both read THIS, they cannot disagree.

    The cost, stated rather than hidden: queue entries occupy slots in the
    20-row no-repeat window, so the effective look-back over songs that really
    played shrinks by the queue depth. `no_repeat_window` is config and re-read
    on every call (euterpe §4), so that is a JSON edit if it ever bites.
    """
    with _lock:
        declined = list(_declined)
    return _played_rows() + _queued_rows() + declined


def _played_rows() -> list[dict[str, Any]]:
    """Songs that have actually been HEARD: the listener's ledger plus the DJ's
    own picks this run.

    Split out from _known_plays because the pop needs exactly this and not the
    queue: an entry must not be refused at play time for sitting in the queue it
    just came off.
    """
    with _lock:
        session = list(_session_plays)
    return euterpe.entries() + session


def _queued_rows() -> list[dict[str, Any]]:
    """The queue as ledger-shaped rows.

    The _session_plays precedent extended one stage earlier: shaped like ledger
    rows so euterpe's views read them with no second matching rule that could
    drift from was_recently_played(), and sourced "dj" so taste_anchor() skips
    them by the rule it already has. A queued row labelled "user" would be the
    2026-09-16 feedback loop again, one step further upstream.
    """
    with _lock:
        return [{"title":    entry["title"],
                 "key":      entry["key"],
                 "video_id": euterpe.video_id(entry.get("url")),
                 "source":   "dj"}
                for entry in _queue]


# ── The turn ──────────────────────────────────────────────────────────────────

def _quiet(snapshot: dict[str, Any]) -> bool:
    """Is this a good moment to take the model and the speakers?

    Conversation wins, with no maximum wait (a long chat just means a quiet
    gap). harmonia.is_playing() covers narration AND an Orpheus briefing —
    Harmonia has one buffer and no channels, so anything audible is in it.

    A player mid-LOAD wins too, and that clause earns its place now that a pick
    can outlive the turn that failed to make it (_owe_a_pick). A track the
    listener just started is SILENT while yt-dlp resolves it, so "nothing is
    audible" is not yet "nothing is playing", and firing here would open a DJ
    pick over the top of a song a person chose two seconds ago. _note_playing
    settles it the moment that song becomes audible; this covers the gap before
    that, which measured seconds wide on a cold yt-dlp.
    """
    return (not snapshot.get("loading")
            and not harmonia.is_playing()
            and not pythia.is_busy())


def _audible(snapshot: dict[str, Any]) -> bool:
    """Is a song actually playing right now?

    The same three fields _observe's first branch reads, and deliberately the
    same test — if these two ever disagreed, the DJ would top up during a gap it
    believed was music, or sit idle through a song it believed was silence.

    Gates the top-up, and that gate is what stops an idle dashboard taking model
    turns all night: banking a song is only free while there is music paying for
    the time.

    PAUSED is not audible, and that clause is not pedantry: a paused player is a
    listener who stopped the music on purpose, the queue is not draining, and
    spending the model to deepen it is work nobody asked for.
    """
    return bool(snapshot.get("running")
                and snapshot.get("title")
                and not snapshot.get("paused")
                and (snapshot.get("pos") is not None
                     or snapshot.get("dur") is not None))


def _top_up(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Bank one more song while the current one plays. Says nothing, ever.

    The turn that costs the listener nothing. Everything expensive about a pick
    — the model, one yt-dlp search per re-pick, 4.2s typically and 17.1s at
    worst — happens here, under a song, instead of in a silence somebody is
    sitting through.

    Two bounds, both load-bearing:

      - only while something is AUDIBLE, so an idle dashboard with Auto-DJ left
        on does not spend the model all night for a queue nobody is draining;
      - only every _FILL_COOLDOWN, because a search that keeps failing plus a
        short queue is otherwise a yt-dlp process every 2s for the length of
        every song. The queue is allowed to fill slowly; it is not allowed to
        try without limit.

    Counts NO failure, and that is the single most important line in this
    function. The breaker means "attempts to fill a SILENCE that produced no
    song", and during a top-up a song is playing — nothing the listener can hear
    has failed. Counting here would let three quiet top-up turns switch Auto-DJ
    off, out loud, in the middle of a perfectly good song.
    """
    global _last_fill_at
    if not _audible(snapshot):
        return _payload()
    now = time.monotonic()
    if now - _last_fill_at < _FILL_COOLDOWN:
        return _payload()
    _last_fill_at = now
    _take_a_turn(filling_silence=False)
    return _payload()


def _looks_like_a_typed_tool_call(text: str) -> bool:
    """Did the model TYPE its tool call instead of emitting one?

    Measured 2026-09-16 (see SYSTEM_PROMPT): when it drops out of tool-calling
    mode it writes 'play_music{query:<|"|>...}' as ordinary content. That is
    text Calliope would happily read aloud."""
    return any(marker in text for marker in _LEAK_MARKERS)


def _names_the_song(intro: str, spoken_name: str) -> bool:
    """Does the model's line actually name the song that is playing?

    The last guard in this module, and the same shape as the rest: the model's
    claim is CHECKED rather than trusted. Measured 2026-09-17 — handed the
    resolved title in the tool's own result, gemma4:e2b announced a different
    Linkin Park song. Earlier measurements caught "Let It All Bleed by The
    Beatles" for 'The Beatles - Let It Be' and a Rhodes Brothers cover
    introduced as The Arctic Monkeys. A name that has to be right is not a
    thing to ask a small model for when the string is already in hand.

    Every part of the name must appear, case-insensitively — usually artist and
    track, so naming the right artist and the wrong song does not pass. A
    substring test rather than anything cleverer: the model is free to write
    "Here's In The End by Linkin Park, ..." in any order it likes, and only has
    to have said the words.

    The known cost, stated rather than hidden: a model that writes "ADIDAS" for
    'a.d.i.d.a.s' fails this and gets the built line. A correct intro replaced
    by a correct sentence is the cheap half of the trade; the expensive half is
    telling a listener they are hearing something they are not.
    """
    said = intro.casefold()
    parts = [part for part in spoken_name.casefold().split(" - ") if part]
    return bool(parts) and all(part in said for part in parts)


def _turn_message() -> str:
    """The per-turn message, built fresh from both lists every time.

    NORMALIZED titles, not raw ones: they are shorter, they are the same strings
    the guard compares against, and a YouTube title that reads like an
    instruction has less room to work once its brackets are gone.
    """
    plays = _known_plays()
    # The anchor reads `source == "user"` and session rows are "dj", so handing
    # it the combined list still yields the listener's own picks and nothing
    # else — one read, three views (the plutus.positions idiom).
    anchor = euterpe.taste_anchor(plays=plays)
    window = euterpe.recent_titles(plays=plays)
    # NOT the tail of `plays`: the ledger no longer holds the DJ's picks and the
    # session list does not hold the listener's, so the newest row of either is
    # not necessarily the song that just ended. _last_played_key is the only
    # thing that saw both.
    finished = _last_played_key
    return (
        f"Just finished: {finished or '(nothing)'}\n"
        f"The listener's own recent picks: {'; '.join(anchor) or '(none yet)'}\n"
        f"Recently played. Do not choose any of these: "
        f"{'; '.join(window) or '(none)'}\n"
        f"Play the next song now."
    )


def _owe_a_pick() -> None:
    """Re-arm the latch after an attempt that produced no music.

    fetch() clears _want_pick BEFORE the turn, which is right — it is what stops
    one ended song firing two turns. But an attempt that played nothing never
    discharged that debt, and until 2026-09-18 nothing put it back. The song
    whose ending armed the pick had already ended, so _observe's trigger could
    not fire a second time, and the breaker — which wants three turns IN A ROW —
    never got its second turn. Auto-DJ went off the air looking ON: no music, no
    spoken notice, the toggle still amber, and not one line above DEBUG.
    Measured 2026-09-16 19:07 — eleven good turns, then one bad one, then
    nothing until the app was restarted.

    Only while the flag is on, and the ordering at every call site does that
    work: _count_failure() runs first and may have just switched it off, and a
    pick owed after that would outlive the flag switched off to stop it.
    """
    global _want_pick
    with _lock:
        if _enabled:
            _want_pick = True


def _count_failure() -> Optional[str]:
    """Advance the breaker. Returns the line to say if it just tripped."""
    global _failures, _enabled, _want_pick
    with _lock:
        _failures += 1
        if _failures < _MAX_CONSECUTIVE_FAILURES:
            return None
        _enabled = False
        _want_pick = False
        _failures = 0
    log.warning("Awen: %d turns in a row produced no song — Auto-DJ is off",
                _MAX_CONSECUTIVE_FAILURES)
    return _BREAKER_LINE


def _take_a_turn(*, filling_silence: bool) -> None:
    """One DJ turn: ask the model for a song and queue whatever passes.

    Returns nothing. It used to decide what was said and what the breaker
    counted; both moved to fetch(), which is the only place that knows whether a
    song actually started playing — and that, not "was a tool called", is what
    the breaker has always meant.

    `filling_silence` says whether anybody is waiting. It drives exactly one
    thing here, the "choosing the next song…" label: a top-up happens UNDER
    music, and telling the operator the DJ is choosing while a song plays would
    make the one label that means "this is why it is quiet" mean nothing.

    Two outcomes now, not three, because the intro is built at enqueue time:
      - the model wrote a usable line  -> it replaces the built one
      - it did not                     -> the built line stands, and it cannot
                                          be wrong, because it is made from the
                                          title the search actually resolved
    """
    played: dict[str, Any] = {}

    def _play(query: str = "") -> dict[str, Any]:
        result = _guarded_play_music(query)
        if "now_playing" in result:
            played.clear()
            played.update(result)
        return result

    global _picking
    t0 = time.perf_counter()
    _picking = filling_silence
    try:
        answer = pythia.ask(
            _turn_message(),
            system=SYSTEM_PROMPT,
            allow_tools=list(_ALLOWED_TOOLS),
            tool_overrides={"play_music": _play},
        )
    finally:
        # In a finally because ask() is documented never to raise and the label
        # is not worth trusting that with: a stuck "choosing the next song…"
        # would outlive the turn and read as a hang forever.
        _picking = False
    took = int((time.perf_counter() - t0) * 1000)

    if not played:
        log.debug("Awen: turn queued nothing in %dms (model said %r)",
                 took, answer[:80])
        return

    # No breaker reset here: a song being QUEUED is not a song playing. That
    # reset belongs where the song becomes audible — see _note_playing.

    # The name comes from the RESOLVED search result, never from the model.
    spoken_name = euterpe.pretty(played["now_playing"], played.get("channel", ""))

    intro = (answer or "").strip()
    if (not intro
            or _looks_like_a_typed_tool_call(intro)
            or pythia.is_degraded(intro)
            or not _names_the_song(intro, spoken_name)):
        # pythia.is_degraded catches the case this line was rewritten for: a
        # turn that PLAYED a song and then ran out of tool rounds returns "The
        # oracle got stuck consulting its tools. Try rephrasing." — prose that
        # is neither blank nor a typed tool call, so it sailed through and was
        # read aloud as the song's introduction. Telling a listener to rephrase
        # a question they never asked is the most confusing thing this feature
        # could say, and the song really was playing, so silence is wrong too.
        log.debug("Awen: intro %r does not name %r — using the built line",
                  intro[:80], spoken_name)
        intro = f"Now playing: {spoken_name}."
    log.debug("Awen: turn took %dms", took)

    # HELD on the QUEUE ENTRY — see "Announcing". The four checks above have
    # already run, so what is stored is the line that passed them; holding is
    # about WHEN it is said, never about what.
    #
    # Held one stage earlier than before, and the invariant is the same one:
    # the sentence lives on the record for the pick it describes, so dropping
    # that pick anywhere — at revalidation, at the cap, at a failed load — takes
    # the sentence with it, for free and with no new rule. Only the lifetime
    # grows, from seconds to minutes.
    with _lock:
        for entry in reversed(_queue):
            if entry["url"] == played["url"]:
                entry["intro"] = intro
                break


def _spoken(say: Optional[str], kind: str,
            now_playing: Optional[str] = None) -> Optional[dict[str, Any]]:
    """A payload only when there is something to say, and None otherwise.

    None has to stay None rather than becoming an empty payload: fetch()
    returns whatever _observe hands back, so an empty one would return before
    the module ever got as far as considering a turn — every tick, for ever.
    """
    if not say:
        return None
    return _payload(say=say, kind=kind, now_playing=now_playing)


def _payload(say: Optional[str] = None, kind: str = "",
             now_playing: Optional[str] = None) -> dict[str, Any]:
    """The dict Kairos hands the rider. Always carries the flag, so the UI can
    follow a breaker trip without a second read."""
    out: dict[str, Any] = {"enabled": _enabled}
    if say:
        out["say"] = say
        out["kind"] = kind
    if now_playing:
        out["now_playing"] = now_playing
    return out


def queue_revision() -> int:
    """How many times the queue has changed. A FREE read — one int.

    For a UI that has to notice a change without paying for a snapshot sixteen
    times a second. The number itself means nothing; only that it differs from
    the one you last drew.
    """
    return _queue_revision


def queue_view() -> list[dict[str, Any]]:
    """The waiting picks, in play order, as rows to draw.

    A SNAPSHOT: new dicts over a copied list, so nothing the UI keeps can reach
    into the entry the worker is about to pop, and a box left open cannot mutate
    the queue by accident.

    Two keys and deliberately not the rest. "id" is what removal takes. "name"
    is what to draw. A URL, a key and the spoken intro are none of a list's
    business, and a snapshot that carries only what it draws cannot leak.

    The name prefers the artist/track pair yt-dlp gave us, because that is the
    one source that KNOWS which half is which — a title has to be guessed at
    from a dash. Both or neither, the same rule has_music_evidence() applies to
    the same two fields; one without the other is not a name. Derived here
    rather than stored at enqueue (§9), so euterpe.pretty() stays the single
    source and a config edit is reflected.
    """
    with _lock:
        entries = [dict(entry) for entry in _queue]
    rows: list[dict[str, Any]] = []
    for entry in entries:
        artist = str(entry.get("artist") or "").strip()
        track = str(entry.get("track") or "").strip()
        if artist and track:
            name = f"{artist} - {track}"
        else:
            name = euterpe.pretty(str(entry.get("title") or ""),
                                  str(entry.get("channel") or ""))
        rows.append({"id": int(entry.get("id") or 0), "name": name})
    return rows


def remove_queued(entry_id: int) -> bool:
    """Take one waiting pick off the queue. True if it was still there.

    The SECOND writer of _queue — the Tk main thread; the Kairos worker is the
    first — and what makes that safe is the discipline already here rather than
    a new one: every mutation under _lock, and every _lock region in this module
    measured in microseconds. None of them spans the model, yt-dlp or
    morpheus.play(), so a UI thread can take this lock without ever waiting on
    a song.

    Identity is the SERIAL, never the index: the worker pops from the head while
    the box is open, so by the time a click lands, row n is a different song.

    A removed song is also DECLINED (see _declined). Without that it leaves the
    do-not-choose list at the same moment it leaves the queue, and the next
    top-up can bank it again inside twenty seconds — the button appearing not to
    work.

    Returns False for an entry already popped, which is not a failure and needs
    no dialog: for the moment between the pop and morpheus.play() the entry is
    a local in the worker and belongs to no list. Removal is scoped to things
    that have NOT started. Reaching further would mean stopping music already
    playing, and this module has no business doing that (see "Tools").
    """
    global _queue_revision
    with _lock:
        for index, entry in enumerate(_queue):
            if int(entry.get("id") or 0) != entry_id:
                continue
            _queue.pop(index)
            _queue_revision += 1
            _declined.append({"title":    entry["title"],
                              "key":      entry["key"],
                              "video_id": euterpe.video_id(entry.get("url")),
                              "source":   "dj"})
            del _declined[:-_DECLINED_CAP]
            log.debug("Awen: %r removed by hand — declined", entry["title"])
            return True
    return False


def _play_from_queue() -> Optional[dict[str, Any]]:
    """Play the next queued song. Returns the entry, or None if none was usable.

    REVALIDATED here, because an entry may be minutes old and two things can
    have changed under it: the listener may have played that song themselves,
    and euterpe's config is re-read on every call, so the duration band or the
    denylist may have moved. Both checks are LOCAL — no network at the pop, or
    the gap this whole feature exists to remove would come straight back.

    Against _played_rows() rather than _known_plays(): the entry must not be
    refused for sitting in the queue it just came off.

    A stale entry is DISCARDED and the next one tried — the same select-rather-
    than-reject shape the pick guard already has, moved one stage later. A
    discard is a guard hit, and guard hits are normal; it counts no failure.
    Returning None when nothing is usable drops the caller straight back onto
    the pre-queue code path, which is the whole framing of this feature: the
    queue is an optimisation whose empty and invalid states are the old
    behaviour.
    """
    global _pending_pick, _queue_revision
    known = _played_rows()
    while True:
        with _lock:
            if not _queue:
                return None
            entry = _queue.pop(0)
            _queue_revision += 1
        if euterpe.was_recently_played(entry["title"], entry["url"],
                                       entry["channel"], plays=known):
            log.debug("Awen: dropping queued %r — played since it was chosen",
                      entry["title"])
            continue
        why = euterpe.is_a_song(entry["title"], entry.get("duration"))
        if why is not None:
            log.debug("Awen: dropping queued %r — %s", entry["title"], why)
            continue
        # from_start: this URL may have a watch-later position from a time it
        # was skipped halfway, and a DJ pick must not open mid-song (measured —
        # see morpheus.play).
        morpheus.play(entry["url"], from_start=True)
        # The seam stops being a cut. Ignored if _maybe_taper already armed one
        # for the song that just ended, which is the case that matters: ONE dip
        # then spans the whole handover rather than two fighting over it.
        morpheus.taper()
        with _lock:
            _pending_pick = dict(entry)
        log.debug("Awen: playing %r", entry["title"])
        return entry


# ── Kairos entry point ────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """Observe, then act if it is time. NEVER raises — see the Contract.

    Kairos's pile-up guard is this job's single-flight: a DJ turn blocks this
    worker for seconds, and the next tick simply skips rather than starting a
    second one. That is why there is no lock around the turn itself.
    """
    global _want_pick               # assigned below, so the read needs this too
    try:
        snapshot = morpheus.last_snapshot()
        announcement = _observe(snapshot)
        if announcement is not None:
            # A song of ours became audible, or the breaker gave up. Either way
            # there is nothing left for this tick to decide: _note_playing has
            # just cleared the owed pick, and a tripped breaker has cleared the
            # flag, so the clause below would refuse a turn in both cases.
            return announcement

        with _lock:
            # `_enabled` here is belt AND braces: _observe only ever arms a
            # pick while the flag is on, so today this clause changes nothing
            # and breaking it leaves the suite green. Keep it anyway — it is
            # what makes "the flag is off" true of the WHOLE module rather than
            # of one function, and a future arming path would otherwise bypass
            # it silently. tests/test_awen pins the composite property.
            owed = _enabled and _want_pick
            # A pick owed outranks a top-up: somebody is listening to silence.
            short = _enabled and not _want_pick and len(_queue) < _QUEUE_TARGET
        if not owed and not short:
            return _payload()
        if not _quiet(snapshot):
            return _payload()          # conversation wins; try again next tick

        if not owed:
            return _top_up(snapshot)
        with _lock:
            _want_pick = False

        # When the queue is warm this is one pop: no model turn, no search, no
        # gap. That is the whole feature.
        entry = _play_from_queue() if _queue else None
        if entry is None:
            # Nothing banked, or everything banked had gone stale. Both are the
            # pre-queue situation exactly, so both take the pre-queue path —
            # the framing worth holding on to is that the queue is an
            # optimisation whose empty and invalid states are the old code.
            _take_a_turn(filling_silence=True)
            entry = _play_from_queue()

        if entry is not None:
            return _payload()

        # Nothing playable. THE breaker's one counting site, and it is here
        # rather than inside the turn because this is the only place that knows
        # a song did not start — which is what "three turns in a row that end
        # with no song playing" has always meant.
        notice = _count_failure()
        _owe_a_pick()
        return _payload(say=notice, kind="notice")
    except Exception as exc:  # noqa: BLE001 — an unprompted job must not take
        # down the tick, and there is nobody watching to see it try.
        log.exception("Awen: tick failed (%s)", type(exc).__name__)
        return _payload()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"[Awen] enabled: {enabled()}  (off at launch, by design)")
    print(f"[Awen] tools:   {', '.join(_ALLOWED_TOOLS)}")
    print(f"[Awen] ledger:  {len(euterpe.entries())} play(s)")
    print("[Awen] the turn message it would send right now:")
    for line in _turn_message().splitlines():
        print(f"    {line}")
    print(f"[Awen] snapshot: {morpheus.last_snapshot()}")
