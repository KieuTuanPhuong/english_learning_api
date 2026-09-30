# Three Highlight Features

Speaker notes for `docs/sequence-diagrams-highlights-simple.puml` (slide version).
Full-detail diagrams: `docs/sequence-diagrams-highlights.puml`.

---

## Highlight 1 — Mock test: the server owns the clock

**In one line:** a timed exam section where every rule that could be cheated —
the countdown, the deadline, the ordering — is decided by the server, and the
browser only draws what the server decided.

**The problem.** A test section is worth something only if its time limit is
real. If the countdown lives in the browser, a student can pause it, edit it, or
just reload the page and get a fresh one. But a pure server-side timer is also
not enough on its own: a student who loses connection for thirty seconds must
not lose thirty seconds of typing.

**Walking the diagram.**

1–4. The clock is not started when the attempt is created — it is started when
the student presses *Start section*. The server stamps `expires_at = now +
duration + 30s` grace and returns it together with its own `server_time`. The
browser compares the two to correct its own clock skew, then renders a
countdown. The row is locked while this happens, so a double-clicked button
cannot mint two clocks.

5–7. While the student answers, the client batches writes: a save is scheduled
3 seconds after the last keystroke, and forced after 10 seconds at the latest.
The answer state is a single draft envelope, replaced whole. Nothing is saved on
the keystroke path, so typing never waits on the network.

8–10. Once `expires_at` passes, the server stops accepting writes and answers
`409 section_expired`. The client treats that as a state change, not an error to
retry: it locks the section. A late write cannot buy time, so the last accepted
draft is exactly what gets graded.

11–15. Submitting turns the draft into one ordinary `Submission` per exercise,
marks the Reading/Listening ones against their answer key, and converts the raw
score into a band through the test format's conversion table. AI grading is
queued, not awaited — the student is not held at a spinner.

**The part worth saying out loud.** The client is deliberately powerless. It
holds no rule it could be talked out of: not section order, not the deadline,
not part gating inside a section. It holds a countdown and a debounce timer.
Everything else is re-checked server-side on every call.

**Also covered by the full diagram:** an abandoned sitting. If a student closes
the laptop mid-section, a cron sweep force-finalizes it ten minutes after expiry,
so an unfinished attempt still produces a report instead of hanging forever.

**Code:** `core/mock_tests.py` (`start_section`, `autosave`, `submit_section`),
`core/views.py` (`TestAttemptViewSet`),
`english-learning-web/components/mock-test/SectionRunner.tsx`.

---

## Highlight 2 — AI grading runs in the background

**In one line:** submitting a section never waits on a language model; grading is
claimed like a job from a queue, runs on a worker, and the report page catches up
by polling.

**The problem.** Marking an essay or a speaking answer means a model call that
can take tens of seconds and can fail. Three things must not happen: the student
must not stare at a loading screen; two workers must not grade the same section
twice; and one failed call must not throw away the answers that did grade.

**Walking the diagram.**

1–2. Grading is *claimed*, not merely enqueued. The server locks the candidate
sections and flips them to `running` in one statement, skipping rows another
worker already holds. Two concurrent callers therefore split the work instead of
duplicating it. The claim is also what makes the *Retry* button safe: a section
stuck `running` past its stale window is claimable again.

3–5. The student opens the report immediately. It renders whatever is already
final — Reading and Listening bands are there straight away, since those are
marked against an answer key, not by a model.

6–10. On the worker, each submission is its own unit of work. Receptive tasks get
a per-question explanation of what went wrong; productive tasks get a rubric
score, which is folded into the section band by weight (IELTS Writing Task 2
counts twice Task 1). If one call fails, the others still land, the section is
marked failed with the reason, and a retry redoes only what is missing.

11–13. While anything is still `running`, the report refetches every four
seconds and fills in. No websocket, no push infrastructure — the state is already
in the row, so polling a few times is enough.

**The part worth saying out loud.** Two rules protect the grade itself. A model
is never called when it cannot add anything — a blank part, or a part where every
answer matched the key, is explained instantly at zero cost. And an AI score is
never written over a teacher's: if human feedback exists, the AI path skips the
submission entirely.

**Code:** `core/mock_tests.py` (`claim_ai_grading`, `ai_grade_section`,
`on_feedback_created`), `core/ai/service.py`, `core/ai/assist.py`,
`english-learning-web/app/(app)/mock-tests/attempts/[attemptId]/report/page.tsx`.

---

## Highlight 3 — Pronunciation: record and get scored

**In one line:** the student reads a target sentence, and one request later the
response body *is* the scored attempt — per-word.

**The problem.** Pronunciation practice is only useful with a tight loop: speak,
see what was wrong, speak again. Anything that makes the student wait, or come
back later for a result, kills the drill.

**Walking the diagram.**

1–3. Recording is native browser capture. The codec differs by browser — Chrome
and Firefox give WebM/Opus, Safari gives MP4/AAC — so the client picks whichever
is supported rather than assuming one. A live level meter runs off the audio
stream, so the student can see the microphone is actually working before
spending a take on it.

4–5. The audio is posted as a file, not as base64 inside JSON. The server checks
it before trusting it: a per-student rate limit of 30 recordings an hour, a size
cap, an allowed content-type family, and a duration cap probed from the file
itself.

6–9. The attempt row is created first, then scored. The engine is chosen by
configuration, not by code: a real speech engine in production, a deterministic
mock in development that returns the same scores for the same audio, which is
what makes the tests reproducible. The engine returns accuracy, fluency,
completeness, prosody and a per-word breakdown.

10–11. The `201` response carries the finished attempt. The page colours each
word by its score and prepends the attempt to the history — no polling, no
second request.

**The part worth saying out loud.** This is the deliberate opposite of Highlight
2. Essay grading is slow, so it is asynchronous and the report polls. Pronunciation
scoring is fast and the student is standing right there mid-drill, so it is
synchronous and the answer comes back in the same round trip. Same codebase, two
different answers, because the two loops have different shapes.

If the engine is misconfigured or unreachable, the failure is loud, but the
attempt row survives with empty scores — a fixed deployment can re-score it
rather than losing the recording.

**Code:** `core/ai/pronunciation.py` (`assess_attempt`,
`get_pronunciation_backend`), `core/audio.py` (`validate_upload`,
`transcode_to_wav16k`), `core/views.py` (`PronunciationDrillViewSet.attempts`),
`english-learning-web/lib/use-recorder.ts`.

---

## Why these three together

They are the three shapes of work the platform has to handle, and each one is
solved differently on purpose:

| | Where the truth lives | How the student waits |
|---|---|---|
| Mock test section | Server clock, server rules | Not at all — writes are batched |
| AI grading | Claimed job on a worker | Report polls until it fills in |
| Pronunciation | Single request | One round trip, answer in the response |
