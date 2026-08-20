# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and this project does not yet have a stable
release.

## [Unreleased]

### Changed

- **M2L is now Cloud Coworker Planning and Orchestration, not a Local Planner MVP.**
  Local-first was quietly doing two jobs: *Cofferdam decides and remembers on hardware you own*, and
  *the model runs there too*. Only the first is load-bearing, and tying the second to it cost the
  quality of the one job that most needs a strong model. Track D measured the gap rather than
  guessing it — Qwen3.5-4B is genuinely good at bounded decisions and genuinely not a deep
  development planner, the 9B is VRAM-bound on this hardware, and a 12B took 8–9 minutes per
  planning decision. So M2L becomes a **provider-neutral planner role** whose first backend is
  expected to be a cloud frontier model, while authority, state, memory, evidence, credentials,
  scheduling and execution control stay local. A local model remains an optional backend and an
  optional specialized capability; it is not kept merely because the server has a GPU.

  The security consequence is the part worth reading: `DESIGN.md` used to say the planner may
  receive rich local context *because it speaks only to loopback*. That was safe while "planner"
  implied "local". It is not safe now, so the rule was rewritten to follow the **endpoint** rather
  than the role — a cloud-backed planner takes the same `CloudContextProjection` as any other
  external model, and being the planner grants it nothing.

  Documentation only. No planner code, no provider backend, nothing deployed. Recorded as
  `DECISIONS.md` D-2026-08-20-1.

### Added

- **M2K PR26 — the same mistake, in the half we thought was safe.**
  PR25 fixed *when* Cofferdam looks at your files. It did not fix when Cofferdam looks at your
  project's **history**, because that check appeared to be settled already. It was not, and the
  version of it that shipped was worse.

  **What went wrong.** Cofferdam records what an agent committed by comparing your project before the
  work with your project after it. The "before" really was taken before. The "after" was taken the
  moment the job was handed over — so for a background agent it was the *same* moment. The record
  therefore read "nothing was committed", and, worse, "and we checked thoroughly".

  **Why nothing else caught it.** Cofferdam's other way of noticing changes is to look at unsaved
  work sitting in your project. But an agent that *commits* its work leaves nothing sitting there —
  committing is exactly what makes that second check come up empty. So both ways of noticing went
  blind at the same moment, and Cofferdam confidently reported that a file it had been asked to
  change was not changed. Not "cannot tell" — **not done**. That happened for every requirement about
  what an agent did: changed a file, created or deleted one, renamed one.

  **What changes for you.** A background agent that commits its work is now credited with it.
  Requirements about what was done are answered from your project's history as it stood when the work
  actually finished. An agent that finishes instantly behaves exactly as before, and an agent that
  changes files without committing is still answered the way it always was.

  **Work that ends badly still counts.** An agent that committed something and then failed or was
  cancelled committed it. That stays in the record.

  **When Cofferdam genuinely does not know, it says so.** If the workstation restarts while an agent
  is running, that turn's requirements now read as unverified rather than being answered from a
  measurement taken before the work started.

  **Nothing was rewritten.** No old record was changed, re-checked against today's files, or
  reinterpreted, and no database migration was needed.

  Unreleased and undeployed: this is the second of two fixes that must both ship before the rolled-back
  deployment is retried.

- **M2K PR25 — a requirement is now checked when the work finishes, not when it is handed over.**
  Cofferdam used to look at your project the moment it passed the job to the agent, and record what it
  saw as "how things ended up". For an agent that finishes instantly that is the same thing. For one
  that works away in the background — which is the normal case — it is a photograph of the room
  before anybody walked in.

  **What went wrong, concretely.** We deployed the whole acceptance stack, asked for one file to be
  created, and the agent created it. Cofferdam still said the requirement was not met, because it had
  already written down "that file is not there" three seconds before the file appeared. Nothing was
  broken about the looking; it looked at the wrong moment.

  **What changes for you.** Requirements about what a file *is* — it must exist, it must be gone —
  are now answered from what your project actually looked like when the work finished. A background
  agent gets the same honest answer an instant one always did. While work is still running, Cofferdam
  says it cannot answer yet rather than guessing, and a question the agent asks you part-way through
  no longer freezes a half-finished snapshot as the answer.

  **Work that ends badly is still described.** If an agent changed some files and then failed or was
  cancelled, those changes are real and are recorded. A requirement can honestly read as met on a task
  that failed — that is a statement about your project, not a claim that the work succeeded.

  **Old records are left exactly as they are.** Anything recorded before this change stays untouched
  and readable as history. What Cofferdam will no longer do is treat it as a current answer, because
  there is no way to tell from the record whether one of those older observations was taken before the
  work or after it. Where that is all there is, it says it cannot verify rather than picking an
  answer. Nothing was rewritten, nothing was re-checked against today's files, and no database
  migration was needed.

  Rolled back, unreleased and undeployed: the deployment that found this was reverted, and this fix
  has to ship before it is retried.

- **M2K PR24 — there is now a screen for saying what a piece of work has to achieve.** PR23 opened the
  door; this walks through it. Until now the only way to declare requirements was to make the HTTP
  request by hand.

  **What you can do.** Starting a task, or sending a follow-up, you can now add requirements — a file
  that must change, a file that must exist or be gone, a rename, a specific create/modify/delete, or a
  note describing something only a person can check. And you say plainly how this turn's requirements
  relate to the last: they are a fresh start, they add to what is already there, or they replace it.

  **It starts at "not declared", on purpose.** The control is on the form with its meaning next to it,
  and until you choose something Cofferdam records that you chose nothing — which is exactly what it
  did before this screen existed. It does not assume a first task starts a fresh set of requirements,
  and it does not assume a follow-up adds to the last one. Those are different intentions that look
  identical from outside, and picking one for you would put words in your mouth about what your work
  is judged against.

  **You never type an internal identifier.** Continuing from a previous turn needs Cofferdam's own
  handle for that turn's requirements; the app reads it for you and shows you which turn you are
  continuing from before you send.

  **Requirements already in force are not re-typed.** If you add to the previous set, you write only
  what is new — the earlier ones carry forward on their own, keeping their identity and their history.

  **A refusal tells you which thing was refused.** A requirement it cannot accept, a tracking choice
  it cannot accept, and "this task moved on while you had the form open" are three different messages
  pointing at three different fixes. Your half-written requirements stay on screen so you can correct
  them, and nothing is quietly re-sent under a weaker choice.

  **What is deliberately not here.** Retiring one specific inherited requirement while keeping the
  rest — the fourth kind of relationship — is not offered yet, and the form says so. Doing it safely
  means showing you exactly which requirements are still live at that point, and nothing Cofferdam
  publishes today can answer that without guessing. A control that guessed could offer you a
  requirement that was retired three turns ago as though it still counted. There is also no suggested
  or auto-written requirement anywhere: everything here is yours.

  **Unchanged.** The connected-assistant credential still cannot set any of this, its request format is
  untouched, and no new endpoint was added — the screen uses the request that already existed.

- **M2K PR23 — you can now tell Cofferdam what a piece of work has to achieve, and how it relates to
  what you asked for before.** This is what makes the whole acceptance feature actually usable.

  **What it adds.** When you start a task or send a follow-up from your own device, you can now attach
  the requirements that work should be judged against, and say plainly how they relate to the previous
  message: this is a fresh start, or it adds to what came before, or it replaces it, or it retires one
  specific earlier requirement. Cofferdam then answers, at each point, whether those requirements were
  met.

  **It will never guess the relationship.** If you do not say, Cofferdam records that you did not say
  — it does not assume a first message starts fresh, or that a follow-up adds to the last one. Those
  are genuinely different intentions that look identical from the outside, and quietly picking one
  would put words in your mouth about what your work is measured against.

  **Only your own device can set them.** The connected-assistant credential cannot, and its request
  format is unchanged — an assistant cannot decide what its own work will be judged against. That
  boundary is the point, not a side effect.

  **A rejected requirement now says so.** If a declaration is not something Cofferdam can accept — it
  names a requirement that is no longer active, for instance — you are told that your declaration was
  refused, rather than that Cofferdam failed to save it. Those are different problems pointing at
  different places to look, and until now they read the same. A rejected declaration also never
  reaches a worker: nothing is started on requirements that were not accepted.

  **What is deliberately not here.** There is no screen for writing requirements yet — building one is
  its own piece of work, and adding a rough version just to say the feature has a user would have been
  the wrong reason. For now this is available to your own device's tooling.

- **M2K PR22 — you can now see whether a piece of work met what you asked for, in the private task
  view.** The first time any of this acceptance machinery becomes visible.

  **What you see.** The turn detail panel already showed what you required and what the machine made
  of each requirement. It now also shows the answer for the turn as a whole: *requirements met at this
  turn*, *a requirement is not met at this turn*, or *requirement assessment incomplete* — plus how
  many requirements there were and how they landed.

  **Deliberately not a verdict on the task.** Every word is scoped to the one point in the task you
  are looking at. There is no "passed", no "failed", no score and no percentage anywhere, because the
  answer is about a single moment and a screen is exactly where that kind of word quietly becomes a
  judgement on the whole job.

  **"Cannot tell" looks different from "not quite".** If Cofferdam could not work out what was
  required — nobody declared it, or the record predates the feature, or something stored looks
  altered — it says so plainly instead of reporting an outcome. Those are shown apart from
  *incomplete*, and a record that disagrees with itself is flagged as something to look at rather than
  smoothed into ordinary uncertainty.

  **Unknown is never shown as zero.** If the set of requirements could not be established, the counts
  read "unknown" rather than a tidy row of zeros, and whether a person is needed reads "unknown"
  rather than "no".

  **Nothing to press.** The section is read-only: no re-run, no approve, no override, no marking
  anything done. It is also private to your own device — the connected-assistant credential cannot
  read it, exactly as before.

  **One honest consequence.** Nothing that creates a task today records how its requirements relate to
  the previous message, so in practice this panel will currently say it cannot assess most work. That
  is the truthful answer, and making it visible is the point: the judging machinery is finished and
  waiting on something to declare its input.

- **M2K PR21 — Cofferdam can now say whether a piece of work met what you asked for.** Nothing is
  visible yet and nothing new is saved.

  **What it adds.** Cofferdam could already say where each individual requirement stood at a given
  point in a task. It can now add those up into one answer for that point: *met*, *not met*, or
  *incomplete*.

  **How it decides.** If even one requirement is definitely unmet, the answer is *not met* — however
  many others were unclear. Knowing one thing failed is not undone by being unsure about something
  else. If nothing is definitely unmet but something is unclear, the answer is *incomplete*. Only when
  every requirement is satisfied is the answer *met*.

  **When it declines to answer at all.** There is a difference between "I know what was required and
  one item is unclear" and "I could not work out what was required". The first is *incomplete*. The
  second is not an answer about your work, and Cofferdam says so plainly instead — including when the
  reason is only that its own bookkeeping has not caught up yet, or that a stored record looks
  altered. It also declines when you never stated any requirements, rather than declaring success over
  an empty list.

  **The distinction it takes most care over.** "You asked for nothing" and "I could not tell what you
  asked for" both mean there is no verdict — but in the first case Cofferdam genuinely knows the
  count is zero, and in the second it knows nothing at all. It reports those differently, and cannot
  accidentally report the second as a tidy row of zeros.

  **When a person is needed.** If you marked a requirement for a human to check, Cofferdam flags that
  separately from the verdict, so a *not met* answer can still tell you somebody is also required. It
  never raises that flag merely because it was unsure about something no person could resolve either.

  **Still one point at a time.** This is a verdict on a single point in a task, not on the task as a
  whole. That larger question remains deliberately unanswered.

- **M2K PR20 — when Cofferdam cannot work out what was required, it now says which of eighteen
  reasons applies.** Nothing is visible yet and nothing new is saved.

  **What it fixes.** Cofferdam works out which of your requirements are standing at any point in a
  task by following the relationships you declared between messages. Sometimes it cannot: nobody
  declared a relationship, or the message predates the feature, or the stored record is damaged. It
  already knew precisely which of those had happened — but when it reported the answer, it flattened
  all eighteen possibilities into one bland "could not work it out". Two genuinely different
  situations became one indistinguishable sentence.

  **Why that mattered.** "Nobody ever said" and "this is too old for me to know" need different
  responses from you: the first is fixable going forward, the second never will be. And "the record
  looks damaged" deserves attention that neither of the others does. Cofferdam had been careful to
  record these as separate facts when it saved them; it was only losing the distinction on the way
  back out.

  **The part that was easy to get wrong.** When the problem is not with the message you asked about
  but with an earlier one it depends on, Cofferdam reports "something this depends on could not be
  worked out" and keeps the real reason underneath. Fixing only the top-level wording would have
  looked like a fix while leaving exactly the two cases above still indistinguishable. Both are now
  carried, along with which message in the chain the trouble was actually found at.

  **Nothing else changed.** Every individual requirement is judged exactly as before. Nothing was
  renamed, nothing new is stored, and none of these situations became an answer about your work —
  they are all still Cofferdam declining to answer, just saying why.

- **M2K PR19 — Cofferdam settles how it will add up "is this done?", before it does.** Documentation
  only. Nothing is visible, nothing behaves differently, and nothing new is saved.

  **What it is.** Cofferdam can now say where each of your individual requirements stands at any point
  in a task. The obvious next step is to add those up into a single answer for that point. The rules
  for doing so were written down some time ago, before the per-requirement answers actually existed.
  This change checks those rules against what was actually built, and fixes the contract before any of
  it is written.

  **The rules held up.** The original doctrine survives intact: known failure beats uncertainty, so if
  even one requirement is definitely unmet the answer is *not met*, however many others are unclear.
  If nothing is definitely unmet but something is unclear, the answer is *incomplete*. Only when every
  requirement is satisfied is the answer *met*.

  **The important distinction it protects.** There is a difference between "I know what was required
  and cannot tell whether one item holds" and "I cannot work out what was required in the first
  place". The first is *incomplete*. The second is not an answer about your work at all, and gets said
  plainly as such — including when the reason is simply that Cofferdam has not finished its own
  bookkeeping yet, or that a stored record looks like it was altered outside Cofferdam. Reporting any
  of those as *incomplete* would quietly hide a problem with the tooling inside a report about the
  work.

  **No requirements is not the same as all requirements met.** If you never stated any requirements,
  Cofferdam will say there is nothing to judge rather than declaring success over an empty list. This
  turned out to be safer than expected: it was checked that there is genuinely only one way a task can
  end up with no standing requirements, so this answer can never be confused with something else.

  **A requirement you asked a person to check still holds the answer back.** Cofferdam has no way for
  a human to record their verdict yet, so any such requirement keeps the overall answer at
  *incomplete*. It is flagged separately so you can see that a person, not the tooling, is what is
  needed — and that flag is never raised just because Cofferdam was unsure about something a person
  could not resolve anyway.

  **Still one point at a time.** This is about a single point in a task, not an overall verdict on the
  whole task. That larger question remains deliberately unanswered.

  **One honesty gap found and written down.** When Cofferdam cannot work out what was required, it
  currently gives one generic explanation where eighteen more specific ones exist underneath — so it
  cannot yet distinguish "nobody ever said" from "this predates the feature" from "the record is
  damaged". Nothing unsafe results; every one of those still refuses to answer. But it is less
  forthcoming than intended, and the fix is recorded to be decided before the adding-up is built.

- **M2K PR18 — Cofferdam can now answer "this file must exist".** Nothing is visible yet and nothing
  new is saved.

  **What it adds.** In the previous change you could write down requirements about what must be
  *true* — that a file exists, or that it does not — and Cofferdam could only say it had no way to
  check them. It can now answer them, using the record it already takes of what was actually in your
  project at the moment each piece of work finished. It does not go and look at your files when you
  ask; it reads what it saw at the time, which is why asking the same question tomorrow gives the same
  answer, and why deleting the project afterwards changes nothing.

  **The part that matters most.** A requirement like "this file must exist" is answered *afresh at
  every point*, not once. If the file was there after the first message, missing after the second and
  back after the third, Cofferdam reports exactly that: satisfied, then not satisfied, then satisfied
  again. It never reuses an earlier answer, so a requirement that quietly broke three messages ago
  stops looking fine.

  **The two kinds of requirement stay separate, on purpose.** "Create this file" and "this file must
  exist" are different requests and get different answers, even about the same file at the same
  moment. If a worker genuinely created a file and something removed it afterwards, Cofferdam says the
  *create* requirement was met — because it was — and the *must exist* requirement was not. That can
  look odd side by side, and it is the honest reading of two different questions. Cofferdam will not
  quietly turn one into the other.

  **Anything counts as existing.** A file, a folder, a shortcut — including a shortcut pointing at
  something that is gone. Something is there, so the path exists. Cofferdam does not follow the
  shortcut to see what is on the other end.

  **When it still says "I don't know".** If it could not safely look at a particular path when the
  work finished — a permissions wall, for instance — it says so rather than reporting the requirement
  as failed. Work that ran before Cofferdam started keeping this record gets the same honest answer.
  "I could not look" is never reported as "it is not there".

  **If the record itself looks wrong, it refuses.** If the stored record of what was in your project
  has been altered outside Cofferdam, or does not match the requirements it claims to be about, the
  whole answer is withheld rather than partly given. It also repairs nothing — a record that had been
  tampered with stays exactly as it is, so the tampering remains visible.

  **Still no verdict.** Cofferdam reports each requirement on its own and does not add them up into a
  pass or a fail. What a mixture of satisfied and unsatisfied requirements *means* is a separate
  decision nobody has made yet.

- **M2K PR17 — you can now write down "this file must exist", even though Cofferdam cannot check it
  yet.** Nothing is visible yet and nothing behaves differently.

  **What it adds.** Until now every requirement you could state was about what a worker *did*: change
  this file, create that one, rename a third. You can now also state what must be *true* — that a path
  exists, or that it does not. Those are genuinely different requests: "create this file" is satisfied
  the moment it is created and says nothing about next week, whereas "this file must exist" is a
  standing requirement that can be checked again and again.

  **What it does not do.** Cofferdam cannot yet answer them. Ask it and it says plainly that it has no
  way to check that kind of requirement — not that the requirement failed. That is deliberate: getting
  the wording into the system first, on its own, keeps the change that stores your requirements
  separate from the change that judges them.

  **It will never rewrite the old kind into the new kind.** A requirement you wrote as "create this
  file" stays exactly that, forever. Nothing converts it into "this file must exist" — that would be
  Cofferdam inventing a requirement you did not ask for, and none of the machinery is allowed to do
  it, not even when carrying requirements forward from an earlier message.

  **The part that needed real care.** Storing a new kind of requirement meant rebuilding the table
  that holds every requirement you have ever set — the first change of this kind this project has
  made. Everything in it is carried across untouched: the same requirements, with the same identities,
  still correctly linked to the judgements and the history that refer to them. The rebuild was
  interrupted at every single step during testing, and every time the result was the old, intact
  database rather than a half-finished one; retrying then works. Older versions of Cofferdam handed
  the new database refuse to open it rather than damaging it.

  **One consequence worth knowing.** Once you have written a requirement of the new kind, going back
  to an older version of Cofferdam would mean losing those particular requirements — the older version
  has nowhere to put them. Before you write any, going back is clean.

- **M2K PR16 — Cofferdam can now say where each of your standing requirements stands, including
  "I don't know".** Nothing is visible yet and nothing new is saved — no screen changes, and nothing
  exposed to your phone or to a connected assistant.

  **What it adds.** Cofferdam could already work out which of your requirements are still standing at
  any point in a task. It can now go one step further and say, for each of them, what it can honestly
  report *at that point* — and, just as importantly, when it cannot report anything.

  **When it can answer.** If you asked for something in the same message that the worker then acted
  on, Cofferdam already checked it at the time, and that existing answer is reused exactly as it was
  recorded. It is not re-checked or recalculated — the original finding is simply pointed at.

  **When it says "I don't know", and why that is the right answer.** If a requirement carried over
  from an earlier message, Cofferdam reports it as unknown. This is deliberate. Everything it checks
  is of the form *did the worker do this during this piece of work* — so re-asking that question
  later would report a file that was correctly left alone as a failure, and reusing the old answer
  would miss anything that broke or got fixed since. Rather than guess in either direction, it says
  it does not know. Requirements you marked for a person to check are also always unknown, because
  nobody has told Cofferdam what a human decided.

  **It will not quietly reuse an old verdict.** A requirement that passed three messages ago does not
  count as passing now, and one that failed then does not count as failing now. Cofferdam is built so
  that the old answer cannot even be worked out from the new one.

  **Things you dropped stay dropped.** If you retired or replaced a requirement, it does not show up
  at all — not as unknown, not as anything.

  **"Cannot check yet" is kept separate from "checked and unknown".** If Cofferdam has not finished
  its own bookkeeping for a piece of work, it says so plainly instead of reporting your requirements
  as unknown. A missing check is never reported as a failure. And if its own stored records ever
  disagree with themselves, it stops and says so rather than guessing or quietly fixing them.

  **Nothing is stored.** All of this is worked out fresh from records that never change, so the same
  question always gets the same answer — even after the project folder is moved or deleted. Asking
  changes nothing on disk.

  **What it still does not do.** It does not tell you whether a task succeeded overall. That needs a
  kind of check Cofferdam cannot make yet — whether something is true *now*, rather than whether
  somebody touched it — which is the next thing to build.

- **M2K PR15 — Cofferdam works out where a "is this still true?" answer would have to be filed.**
  This change is documentation only. Nothing is saved, no screen changes, and the app behaves exactly
  as it did yesterday.

  **The question it settles.** Cofferdam can now see what is actually in your project when a worker
  finishes. The obvious next step is to use that to say whether a standing requirement is currently
  satisfied. Before writing any of that, one boring-sounding question had to be answered: when
  Cofferdam decides "yes, that file is there", **where does it write that down, and what does it have
  to keep alongside it so the answer can be trusted later?**

  **What it found.** The place Cofferdam already keeps its judgements turns out not to fit. Those
  records mean something quite specific — *this is what we thought about the requirements you gave us
  for this one piece of work, based on what the worker did during it*. They have no way to say "this
  requirement came from a request you made three messages ago", and they only have room for one
  verdict per piece of work, so a second kind of judgement cannot sit beside the first without
  muddling both. Some of that turned out to rest on habit rather than on anything the database
  enforces, which was worth knowing.

  **What it decides.** New-style answers get their own place, and the existing records keep their old
  meaning untouched — nothing already written gets quietly reinterpreted. Each new answer records both
  *when you first asked for this* and *when we checked it*, which are genuinely different things, and
  it points at the exact observation it was based on so it can be re-checked rather than taken on
  faith.

  **History is kept.** If a requirement is satisfied, then broken, then fixed, Cofferdam keeps all
  four answers rather than a single running status that overwrites itself. The record of something
  breaking and being fixed is usually the useful part.

  **What it refuses.** Cofferdam will not turn "please create this file" into "this file must exist
  forever". Those are different requests, and only you can make the second one — so requirements about
  what must currently be true have to be written as such, and nothing will convert one into the other
  behind your back. And if it could not check something, it says so; it never reports a requirement as
  failed just because it was unable to look.

  **What it does not do.** No new checks, no new requirement types, and it still does not tell you
  whether a task succeeded. It also writes down the one obstacle in the way: accepting a new kind of
  requirement means rebuilding a table that holds history, which is a riskier change than anything
  this project has done so far and gets planned as one.

- **M2K PR14 — Cofferdam now writes down what was actually there when a worker finished.**
  Nothing about this is visible yet, and that is deliberate. No screen changes and nothing new is
  exposed to your phone or to a connected assistant.

  **The gap it fills.** Everything Cofferdam records about a piece of work is a note about *what the
  worker did*: it changed this file, it created that one, it renamed a third. Nothing it stores says
  *what your project looks like now*. That is why it still cannot tell you whether a request carried
  over from an earlier message is satisfied — the old note is about an old moment, and re-asking
  "did somebody touch this file?" about a later piece of work would report a file that was correctly
  left alone as a failure.

  **What it now records.** When a worker finishes, and before the piece of work is written down as
  closed, Cofferdam looks at the files your requirements actually name and notes whether each one is
  there — a file, a folder, a shortcut, or nothing at all. That note is saved once and never
  changes. Asking about it later reads the saved note; it does not go and look again, so a piece of
  history cannot quietly rewrite itself because you have edited the project since, and moving or
  deleting the project afterwards changes no past answer.

  **It looks at the project as it really is.** If a worker deleted a file without committing it,
  Cofferdam says the file is gone, because it is. If a worker created one without committing it,
  Cofferdam says it is there. It records which committed version the work sat alongside, but the
  files on disk are what it believes.

  **It only looks at what it was asked about.** Never the whole project — just the files your
  standing requirements mention. If Cofferdam cannot work out what your requirements currently are,
  it records that it could not, rather than looking at a plausible-seeming list instead.

  **It will not follow a shortcut out of your project.** If a folder in the path is a link pointing
  somewhere else, Cofferdam refuses to look through it rather than reading whatever is on the other
  side, and it never describes anything outside the project. A shortcut that is itself the thing
  being asked about is reported as a shortcut, without following it — including a broken one, which
  is still something rather than nothing.

  **"Not there" and "could not look" are kept apart.** If a file is missing, Cofferdam says so. If it
  was blocked — no permission, a refused shortcut, a read error — it says *that* instead, and never
  quietly files it as "missing". Nothing reads a failure to look as proof of absence.

  **It records nothing about contents.** No text, no size, no timestamps, no permissions, no listing
  of what is inside a folder. Only whether something is there, and what sort of thing it is.

  **If it cannot look, your work is still your work.** A project Cofferdam could not read is a gap in
  its own notes, not a fault in what the worker did. A finished piece of work stays finished.

  **What it does not do yet.** It still does not tell you whether a task succeeded, and it does not
  change any existing judgement. Noticing that a file exists does **not** turn "the worker was
  supposed to create this file" into a pass — those are different questions, and joining them is the
  next thing to be designed rather than something slipped in here.

- **M2K PR13 — Cofferdam writes down why it still will not tell you whether a task is done.**
  This change is documentation only. Nothing is saved, no screen changes, and the app behaves exactly
  as it did yesterday.

  **The problem it names.** Cofferdam can now work out which of your requirements are still standing.
  The obvious next step is to say whether they are *met* — and that turned out to be unsafe for a
  reason worth writing down before building anything.

  Everything Cofferdam currently checks is a question about **what the worker did during one
  particular turn**: did it change this file, did it create it, did it rename it. None of those is a
  question about **what your project looks like now**. So when a requirement carries over from an
  earlier request, there are three tempting ways to judge it and all three are wrong. Reusing the old
  answer misses both later breakage and later fixes. Re-asking the question about the newest turn is
  worse than imprecise: a requirement satisfied earlier and correctly left alone since would be
  reported as *failed*, most often exactly when the work was right. And the fact that you still want
  something does not prove it is still true.

  **What it decides.** No old answer is ever reused as a current one — not a pass, not a failure, not
  an "unknown". If Cofferdam cannot establish where a requirement stands right now, it says so
  instead of guessing in either direction.

  **What it admits.** Cofferdam can sometimes show that something later broke a requirement. It
  cannot show that nothing did, because it only watches while a worker is running — anything that
  happens in between is unseen — and because what it records is *changes*, not a picture of the
  project. Closing that gap needs a genuinely new kind of check: whether a file exists now, rather
  than whether somebody touched it. That is written down as the next thing to build, and until it
  exists the honest answer to "is this task done" stays unavailable.

- **M2K PR12 — Cofferdam no longer refuses a sensible way of retiring a requirement.**
  Nothing about this is visible yet, and that is deliberate.

  **The problem it fixes.** Say you asked for two things, then came back and asked for a third
  without changing the first two, and then came back again wanting to drop the *first* one. That is
  an ordinary way to work, and Cofferdam refused it. It would only let you retire a requirement that
  had been named in the immediately preceding request — not one that was still standing from earlier.
  The only way through was to point at the older request instead, which quietly discarded everything
  the middle one had added.

  **What changes.** You can now retire anything that is actually still standing, whenever it was
  first asked for. Cofferdam works out what is currently required and checks your request against
  that, which is the same thing it already did when reading the list back.

  **What it will not do.** It still refuses to retire something that is no longer standing — you
  already dropped it, or a later request replaced everything before it. It still refuses a
  requirement from a different task, or one that does not exist. And if it cannot work out what was
  required before, it now says so and refuses the request outright rather than accepting something it
  cannot make sense of. Any refusal leaves nothing behind: it is all-or-nothing, and the worker is
  never started.

  **What it does not do yet.** It still does not tell you whether a task succeeded. Nothing new is
  saved to disk, no stored record changes meaning, no screen changes, and the app behaves exactly as
  it did before.

- **M2K PR11 — Cofferdam can now work out which of your requirements are still standing.**
  Nothing about this is visible yet, and that is deliberate.

  **The problem it fixes.** Cofferdam already stored what you asked for on each turn, and — since the
  last change — how each new request related to the one before it. What it could not do was put the
  two together and say *so what is actually required right now*. Anything that wanted to answer
  "is this task done" had to work that out for itself, which is exactly where a system starts
  guessing.

  **What changes.** Cofferdam can now answer it. If your latest request adds to the previous one, the
  earlier requirements are still standing and the new ones join them, in the order you wrote them. If
  it replaces the previous one, only the new ones stand. If it retires specific earlier requirements
  and keeps the rest, the retired ones drop out and everything else stays exactly where it was.

  **What it will not do.** It will not guess. If nobody ever said how one request related to the one
  before — or if the turn is old enough to predate that being recorded — Cofferdam says *unknown*
  rather than picking something plausible. It never gives a partial answer: half an answer is the one
  thing more dangerous than none.

  **One thing worth knowing.** An unknown patch of history is no longer permanent. The moment you
  state that a new request *replaces* what came before, the requirements are knowable again, because
  "replaces" does not depend on knowing what it replaced.

  **What it does not do yet.** It still does not tell you whether a task succeeded, and an empty list
  of requirements does not mean it passed — it means nothing is currently required. Nothing is saved
  to disk by any of this; the answer is worked out fresh each time from records that cannot change.
  No screen changes, no new button, and the app behaves exactly as it did before.

- **M2K PR10 — Cofferdam can now be told how a new request relates to the last one.**
  Nothing about this is visible yet, and that is deliberate.

  **The problem it fixes.** When you come back to a task and ask for something more, Cofferdam had no
  way of knowing what you meant about the earlier request. Did the new one replace it? Add to it?
  Retire one part of it and keep the rest? It stored both lists of requirements and nothing about how
  they relate — so it could not honestly answer "did this task do what I asked" without guessing, and
  guessing wrong in either direction gives a confidently wrong answer.

  **What changes.** Before a worker is allowed to start, Cofferdam now records the relationship
  alongside the requirements themselves: this is the first request, or it adds to the previous one,
  or it replaces it, or it keeps the previous one except for specific parts it names. Where one
  requirement retires another, that link is recorded explicitly — pointing at the exact earlier
  requirement, never by matching words, because two unrelated requirements can be worded identically.

  **What it will not do.** It will not guess. If nobody says how a new request relates to the old one,
  Cofferdam writes down that nobody said, rather than picking a default and hiding it. Old tasks from
  before this change get nothing written at all — no reading of their prompts, no assuming the newest
  request wins. And only you, or a future planner Cofferdam itself owns, can state a relationship; the
  worker doing the job cannot, because a worker that could retire its own requirements could retire
  the one it had just failed.

  **What it does not do yet.** It still does not tell you whether a task succeeded. That answer needs
  rules that were written down separately and are not built, and nothing here computes one. No screen
  changes, no new button, and the app behaves exactly as it did before.

- **M2K PR9 — Cofferdam writes down the rules for judging a task, before it can judge one.**
  This change is documentation only. No screen changes, no new button, nothing new is saved, and
  the app behaves exactly as it did yesterday.

  **The problem it addresses.** Cofferdam can now answer, for a single turn, whether each individual
  requirement was met, not met, or could not be verified. What it still cannot answer is the question
  people actually ask: *did this task do what I asked?* Turning a list of individual answers into one
  overall answer sounds like arithmetic and is not — it is a series of judgement calls about what
  counts as good enough, and every one of them can be got wrong in a way that reads as success.

  **What it settles.** That a worker finishing cleanly is not the same as the work being acceptable.
  That one clearly unmet requirement is enough to say the requirements were not met, but that
  *uncertainty* — Cofferdam being unable to check something — must never be reported as failure, and
  must always block a clean pass. That a requirement only a person can judge keeps the answer
  incomplete until a person actually judges it, and is never quietly marked done on a worker's say-so.
  That "no requirements were given" and "this task is too old for us to know what was asked" are two
  different sentences and neither one means success.

  **What it deliberately refuses to decide.** Whether a whole task — across several rounds of
  back-and-forth — succeeded. If a later round changed its mind and asked for a file to be removed
  that an earlier round asked for, treating both as live makes the task contradict itself. If only
  the newest round counts, the original feature quietly stops mattering. Cofferdam does not currently
  record whether a later request replaces, extends or is unrelated to an earlier one, so it says the
  task-level answer is unavailable rather than inventing one. Saying "I don't know yet" is the
  feature here.

  **Why now, before the next piece of work.** The next milestone lets Cofferdam run project checks —
  the first time it will run a command inside somebody's project. Those checks produce more results,
  and results need a consumer. Writing the rules first means the checks are built against a contract
  that already exists, instead of the contract being decided by whatever the checks happened to do.

- **M2K PR4 — Cofferdam now writes down where a project stood before a worker touched it.**
  Nothing about this is visible yet, and that is deliberate.

  **The problem it fixes.** A worker that edits files and then *commits* them leaves the project
  looking untouched. Cofferdam's existing check asks Git "what is different from the latest commit?",
  and after the worker commits, the latest commit is the worker's own — so the honest answer becomes
  "nothing", and the work disappears from the evidence view. That has been a known hole since PR3
  shipped, recorded rather than hidden.

  **What changes.** Before a worker is allowed to start — on a new task and on every follow-up —
  Cofferdam reads the project's current Git revision and whether the folder already had uncommitted
  changes, and saves it. Only then does the worker begin. Cofferdam reads this itself; the worker
  cannot suggest it, the task description cannot influence it, and nothing arriving over the network
  can choose it.

  **What it does not do yet.** It does not compare anything. Nothing in the app looks different,
  no new screen or button appears, and the evidence panel is unchanged. Using the saved point to
  show what a worker committed is the next piece of work, kept separate on purpose: a starting line
  recorded in the wrong place would make everything measured from it wrong while looking exactly as
  authoritative.

  **What it will not pretend.** If the project is not a Git repository, or has no commits yet, or
  Git cannot be read, Cofferdam records *that* — it never invents a starting point, and your task
  still runs normally. And a recorded starting point cannot prove the worker was the only thing that
  changed the folder afterwards; you, an editor saving in the background, or another tool can too.
  What it supports is "this changed since here", not "the worker did this".

  **If Cofferdam is interrupted mid-task, the saved point survives untouched.** A worker that had
  already started — and may already have committed something — must never have its starting line
  redrawn behind it, so once Cofferdam has handed control to a worker the saved point is frozen, even
  if the interruption means Cofferdam never got as far as recording the turn. A retry uses the
  original point rather than a fresh one.

  Existing tasks and turns from before this change get no starting point, and none is guessed for
  them. Still no pass, no fail, no score, no verdict.

- **M2K PR3 — Cofferdam can now say *what* changed, not just *that* something did.** The evidence
  panel used to tell you that the worker and Cofferdam both named `src/foo.py`, and then have to
  admit it could not say whether the file was created, edited, deleted or renamed. It can now,
  because it was already asking Git that question and throwing the answer away.

  **What you see.** *Machine observed: created / modified / deleted / renamed*, and for a rename
  both names, in order. Against the worker's own claim it says **Operation agreed**, **Operation
  differs**, or — honestly, and often — **Operation not established**.

  **"Operation differs" is not an accusation.** It means the two records describe different things,
  and both are kept exactly as they were. An agent that edits a file and then deletes it produces
  this and has done nothing wrong. There is still no pass, no fail, no score and no verdict anywhere
  in it.

  **New folders are listed file by file.** A newly created folder used to show up as just the
  folder, which could never line up with a claim about one file inside it. Every file is listed now.

  **Files with awkward names stopped disappearing.** A file called `has space.txt`, or one with an
  arrow or an accent in its name, used to produce *no evidence at all* — Git quotes those names and
  Cofferdam skipped them. They come through now.

  **And it tells you when it did not see everything.** If Git reported more changes than Cofferdam
  recorded, the panel says so, so a file with no observation is not mistaken for a file that did not
  change. One limit worth knowing: Cofferdam looks at what is *uncommitted*, so if an agent commits
  its own work, there is nothing left for it to see — and the panel says the claim was unmatched
  rather than pretending nothing happened.

- **M2K PR2 — see what the worker said it changed, and what Cofferdam actually saw.** An agent
  finishes a turn and tells you it edited three files. Until now that sentence was all you had:
  Cofferdam recorded what the agent *claimed* and, separately, what it *observed* by running
  `git status` itself — and nothing put the two side by side. Open a task and press **Evidence**,
  and it does, one turn at a time.

  **Three things, kept apart on purpose.** *Worker claims* is what the agent said. *Machine
  observations* is what Cofferdam went and looked at. *Relationships and gaps* is where they meet:
  **Path agreed** when both name the same file, **Claim only** when the agent reported something
  Cofferdam has no observation of, **Observed only** when Cofferdam saw a file change that no claim
  mentions.

  **"Path agreed" means the file, not the edit.** Cofferdam's check today can tell you *that*
  `src/foo.py` changed. It cannot tell you whether it was created, modified, deleted or renamed — so
  the panel says **"Operation not established"** on every row, including the agreeing ones. There is
  no PASS, no FAIL, no score, no confidence and no risk level anywhere in it, because none of those
  is a thing the evidence supports. "Claim only" is not an accusation either: an agent that changed
  a file and committed it leaves a clean tree, and there is simply nothing to match against.

  **It tells you when its own record is short.** If some of what the agent reported could not be
  stored, the panel says *Claim set incomplete* and how much landed. If no report was recorded at
  all, it says that too rather than pretending the set was complete.

  **It reads records, not your repository.** The panel is assembled entirely from what was written
  down at the time — it runs no commands, opens no files and calls no model. Open it a year later
  and it still describes the turn as it happened, not the repository as it is now. Reading it
  changes nothing: no task is touched, no agent is woken, and nothing is added to a task's history.

  **Turns stay separate.** Evidence from turn two can never be shown against turn one's claims, even
  for the same file. Turns that ran before Cofferdam started recording turn boundaries say
  **"Legacy turn attribution unavailable"** and show no observations at all — an absence there is
  not a finding about the work, and the panel says so rather than letting a blank space imply one.

  It is on your phone and your workstation, behind your device token. The ChatGPT bridge cannot
  reach it.

- **M2J PR2 — Cofferdam can read your memory, and can be *allowed* to change it.** Memory is
  Markdown you own: your project's own documents, and a dedicated
  [Obsidian](https://obsidian.md)-compatible vault for the personal, cross-project things. Both are
  readable **by role** — you tell Cofferdam once, on the workstation, that `status` means
  `STATUS.md` — so nothing that talks to Cofferdam ever names a file, a folder or a path. Setup and
  the full model are in [`docs/MIND.md`](docs/MIND.md).

  **The vault does not exist until you say where it is, and say yes.** There is no default
  location, nothing scans your home directory or an existing Obsidian vault, and no request can
  grant one: you write the path down once in `config/mind-grant.json` **and set `"enabled": true`**.
  Writing the file is not enough on its own — this one setting is stricter than the rest of
  Cofferdam's configuration on purpose, because it is the only thing standing between your personal
  notes and everything else. Delete the file, or set it back to `false`, and Cofferdam has no
  personal memory at all — including for a change that was already waiting for your approval. Your
  vault is plain Markdown in an ordinary folder and works in a text editor with Cofferdam stopped —
  Obsidian is not required and is never launched.

  **Nothing writes to your memory without you saying yes.** A change is queued as a *proposal*,
  which touches nothing on disk, and you read it and decide. When you accept, Cofferdam re-reads
  the document and checks it is still exactly what you reviewed. **If you edited it in the
  meantime, the change is refused rather than dropped on top of your edit** — you read it again and
  propose again. It also checks that the role still points at the *same document*: if you rewired
  which file `status` means, the change is refused even when the new file happens to say the same
  thing. Applying replaces one file atomically: it either fully happens or the file is untouched,
  and nothing else in the project or the vault is altered.

  **If the machine stops mid-apply, Cofferdam tells you the truth about it.** On the next start it
  looks at the document and works out whether the change landed or not — and if it did not, it
  says so and waits for you rather than quietly finishing the job. A restart never writes to your
  memory on its own.

  **Nothing can delete your memory.** There is no delete, rename or move operation to reach — not a
  blocked one, an absent one — and a proposal that would empty a document is refused as the
  deletion it is.

  **Only your own device can accept.** The private Custom GPT cannot see any of this, and there is
  no acceptance route for a model or an assistant of any kind. Reading is local, too: this release
  sends memory to nothing and nobody.

- **M2J PR1 — Cofferdam knows what you are working on, and remembers it.** Until now every surface
  worked that out again per request: the phone knew because you were looking at a task list, the
  Custom GPT knew because the conversation mentioned a project, and nothing survived a restart.
  Now there are **workspaces** — a name for a piece of work, bound to a project you already have —
  and each one keeps its own **objective**, its own expected next step, and a pointer to the task
  in flight. Configure them in `config/workspaces.json`; setup and the full API are in
  [`docs/WORKSPACES.md`](docs/WORKSPACES.md).

  **Switching workspaces does not smear one onto the other.** Context is kept per workspace, so
  moving from one to another and back finds each objective where you left it, rather than showing
  you yesterday's goal for something you are not doing.

  **Nothing is remembered that could go stale.** A task's state, and which worker runs in a
  workspace, are looked up fresh every time you read — from Task Core and from the project. A saved
  copy would be right for a few seconds and then quietly wrong, which is the one thing this kind of
  "current status" feature is usually bad at.

  **A finished task stays on screen.** When work completes, fails or is cancelled, the reference is
  kept and labelled rather than cleared: it finished, and that is exactly the moment you come back
  to look at it. A task that no longer exists says so, with its id, instead of vanishing.

  **A workspace can only name a project.** There is no field for a folder, an agent, a model or a
  provider — those decisions live where they already lived, and writing one here is refused with a
  message saying where it belongs. There is no way to create a workspace over the network, and
  Cofferdam never invents one for a project it finds.

  **Nothing here runs anything.** "Expected next step" is a note to yourself; Cofferdam records it
  and does not act on it.

  If you configure no workspaces, nothing changes: every existing task, Claude and Custom GPT flow
  works exactly as before, and no new database is created.

- **M2D — press play on the track you picked, from your phone.** Cofferdam could find the exact
  song and open Spotify; you still had to press play yourself. This adds control of your **own
  Spotify account's player**: what is playing now, pause and resume, previous and next, Spotify's
  own volume, its Connect devices, and *Play now* / *Add to queue* on a track you chose from a
  search result. Setup and troubleshooting in
  [`docs/SPOTIFY_PLAYBACK.md`](docs/SPOTIFY_PLAYBACK.md).

  **You authorize once, in Opera on the workstation.** Authorization Code with PKCE, which needs no
  client secret — so the catalogue-search secret already on this host never enters the flow. The
  redirect is the loopback URI `http://127.0.0.1:8888/callback`, which Spotify's current rules
  permit and which `localhost` would not satisfy; the temporary listener binds to `127.0.0.1` and
  nothing else, serves exactly one path, and stops on success, failure or timeout. `127.0.0.1` on a
  phone *is the phone*, so the PWA says "continue in Opera on the workstation" instead of leaving
  you waiting for a tab that cannot arrive, and the attempt expires on its own rather than hanging.

  **The refresh token is stored `0600` in a `0700` directory, written atomically**, in its own file
  separate from the catalogue credential; the access token is never written to disk. A refresh
  response that omits a new refresh token **keeps** the one already held — Spotify documents that
  this happens, and the naive reading would disconnect a working account at the next restart.
  *Disconnect* removes the local authorization and says plainly that it did **not** revoke access at
  Spotify, because the API publishes no revocation endpoint for this flow; the guide says where to
  do that half.

  **No action claims what it did not see.** Every player write answers `204 No Content` — Spotify
  acknowledging the request, not the speaker changing — so each action re-reads playback and
  compares, with `requested` and `observed` as separate keys. Playing a chosen track verifies the
  item now playing *is* the item you asked for. Adding to the queue reports that Spotify accepted it
  and explicitly does not claim playback started.

  **A Spotify device id is not an identity** — the documentation says "persistent to some extent"
  and allows it to be absent — so the phone only ever holds an opaque handle, re-resolved against a
  freshly read device list before any device-targeted action, with no fallback to matching a device
  by name. Restricted devices, which Spotify documents as accepting no Web API commands at all, show
  their controls as unavailable rather than as buttons that can only fail.

  **Spotify has no mute, so Cofferdam's mute is volume zero and says so** — the flag is
  `muted_by_cofferdam`, never `muted`. Unmute restores the level Cofferdam recorded, and when there
  is none it **refuses and asks you to choose one** rather than picking a number nobody asked for.
  Spotify's volume and the computer's volume stay two clearly labelled controls in two panels.

  **Play now sends a search id and a result id and nothing else** — the server rebuilds the Spotify
  URI from the session it privately remembers, so there is no field for a URI, a track id or a
  device id anywhere in the request. Track results only; albums, artists and playlists keep *Open in
  Spotify*, because those are contexts and inventing "play this artist" would be inventing
  behaviour nothing verified.

  **What you listen to stays yours.** Audit records carry the operation and the outcome and nothing
  else — no track, artist, album, query, account or device id. Nothing is written to the daemon log,
  including by the callback listener whose default access log would have contained the authorization
  code. No listening history is kept, and the panel makes no browser console call.

### Fixed

- **M2D.1 — press Play once, with Spotify closed, and get the track you picked.** Real validation
  from the phone found three failures. All of them came from the same habit: looking **once** and
  believing what was seen.

  **Spotify closed no longer means "no device".** Play now opens the installed desktop application
  through the same allowlisted launcher the Media panel uses — no shell, no command line built here,
  and never a web page opened as a substitute — then waits a bounded time for the Connect device to
  register and starts the track you asked for. Spotify open *but idle* also used to be refused; that
  device is now made active first with the documented transfer operation, which is why "Open in
  Spotify, then Play now" was a working workaround and is no longer needed.

  **A single immediate read was denying changes that had happened.** Spotify's player endpoints are
  eventually consistent, so the read taken microseconds after a write frequently still described the
  world before it: setting 80% reported *"set to 80% but the device reports 50%"*, and the first Play
  now reported *"playing something other than the track you chose"*. Every observation now uses a
  bounded confirmation schedule — an immediate first read, then a fixed number of further reads, then
  a truthful give-up. Playback is re-*read*, never re-sent.

  **An older answer can no longer win.** In the phone, a state poll issued *before* a write could
  resolve *after* it and repaint the old value over the newly verified one — which is why 50 → 80 left
  the slider showing 50. Every request that produces state now carries a monotonic generation, older
  responses are discarded, in-flight reads are cancelled when a write begins, and periodic polling
  pauses until the write is confirmed.

  **Nothing loops.** One launch attempt per recovery, one transfer attempt, fixed attempt counts on
  every wait, and a bounded overall timeout. When recovery cannot finish, the panel says which step it
  reached and offers Retry — once, and only for refusals a second attempt could genuinely fix.

  **And it says what it is doing.** Cold start can take twenty seconds, so the panel shows *Opening
  Spotify… / Waiting for Spotify device… / Starting selected track…*, read from a route that touches
  neither Spotify nor the filesystem. Each phase is written by the code about to do that thing, so the
  sequence is a log rather than a script. Where several devices are available and none is active,
  Cofferdam **asks** instead of picking: choosing the first of three speakers would start music in a
  room nobody named.

- **M2C — turn the volume down from your phone, and be told the truth about it.** Cofferdam could
  open Spotify and pick the exact track; it could not change how loud the room was. This adds
  reading and safely controlling the workstation's real PipeWire/WirePlumber audio: the current
  output, the outputs actually connected, system volume, mute, and what is currently making sound.

  **These are the first routes that change the physical machine**, so the surface is the narrowest
  in the service: a runtime resource id in the path, an integer percentage, and a boolean. There is
  no field for a node id, a device name, a PipeWire property, a profile, a command or a program —
  absent from the schemas rather than validated and rejected — and unknown fields are refused
  rather than ignored. No shell is constructed; every backend call is a fixed argument vector.

  **A PipeWire node id is never an identity.** The daemon reuses those integers once their object is
  destroyed, so an output is addressed by a digest over host, audio-graph cookie and stable node
  name, and both the node name and PipeWire's monotonic serial are re-verified against a fresh graph
  read immediately before acting. An id from before an audio-server restart resolves to nothing
  rather than to whatever now occupies the slot.

  **The volume number matches the one on the laptop screen.** PipeWire stores gain linearly while
  `wpctl` and GNOME use a cubic perceptual scale — 0.846138 linear reads as 0.95 through `wpctl` —
  so publishing the stored value would have shown 85% for a speaker the desktop calls 95%. Volume
  is read and written through one interface on one scale, with no curve assumed anywhere. Values
  above 100% are not offered, and out-of-range input is refused rather than clamped.

  **No action claims a success it has not observed.** `wpctl` exits zero for a command it merely
  accepted, so every action re-reads the host and compares; `requested` and `observed` are separate
  keys in every response. Choosing a different output reports what the streams actually did — if
  music that was already playing stayed behind, the phone says so instead of reporting a clean
  switch.

  **Moving one playing stream is published as `unavailable` with its reason, not implemented.**
  WirePlumber offers no command for it, and the metadata workaround would address a stream by its
  transient node id and leave that application pinned to that output for future sessions.

  **What is playing is never read.** Stream fields are built from an allowlist, so the track or
  video title in `media.name` cannot leak; an application is named only through the daemon's
  kernel-verified `pipewire.sec.pid`, resolved through `/proc` to an exact executable match, and
  anything less stays unclassified with a reason. The audit records the operation, resource and
  observed outcome — and deliberately not the volume level.

  Documented in [`docs/AUDIO_CONTROL.md`](docs/AUDIO_CONTROL.md), decided in
  [`DECISIONS.md`](DECISIONS.md) D-2026-08-05-9.

- **A complete Spotify and YouTube credential setup guide**
  ([`docs/MEDIA_PROVIDER_SETUP.md`](docs/MEDIA_PROVIDER_SETUP.md)) — console walkthroughs for both
  providers including the Web API selection, why the Spotify redirect URI is irrelevant to
  catalogue search, restricting the YouTube key and leaving service-account authentication off; the
  exact file schema and permissions; how to validate the configuration without printing any
  credential value; where credentials must never be put; and troubleshooting for each provider
  state.

- **M2B3A.1 — real search results you can pick from, for Spotify and YouTube.** M2B3A could open a
  service's search page; it could not answer "which of these is the one I meant?". This adds
  official catalogue search — the Spotify Web API and the YouTube Data API v3 — with up to five
  result cards, and opens the **exact** item chosen: the selected track in the native Spotify
  application, the selected video in Opera.

  **The client never names a destination.** Search returns opaque handles; opening one names a
  search session and a result, and the server re-resolves both from its own memory and rebuilds the
  launch target from validated identifiers. No request schema has a field for a URL, a URI or a
  video id, and unknown fields are refused rather than ignored. Search sessions are in-memory and
  bounded — 600 s, 32 concurrent, 5 results, gone on restart — and a result cannot be opened through
  a provider that did not produce it.

  **Credentials never leave the host.** They live in `$COFFERDAM_HOME/secrets/media_providers.json`
  (0600), beside the device token; there is no credential form in the PWA, and the only observable
  thing anywhere is a status word — never a value, prefix, length, or even the file's path. One
  stdlib module makes every provider call, with a fixed host allowlist, verified TLS, bounded
  timeouts and response size, and **redirects that are never followed**.

  **Nothing claims playback**, and playback control is unreachable rather than merely
  unimplemented: Spotify's client-credentials flow reaches only non-user endpoints. "Open first
  result" is an explicit button, never automatic; the persistent auto-open-first preference is
  deferred.

  With no credentials configured the phone says "structured results not configured" and every
  M2B3A action keeps working untouched. Netflix, Prime Video and TV+ are unchanged and cannot gain
  structured search — their catalogue entries carry no adapter key. Documented in
  [`docs/MEDIA_RESULTS.md`](docs/MEDIA_RESULTS.md).

- **M2B3A — media and application launch profiles.** Spotify, YouTube, Netflix, Prime Video and
  TV+ are reachable from the phone as launch definitions, through two typed actions —
  `open_media_provider(provider_id)` and `search_media_provider(provider_id, query)` — plus a
  read-only catalogue at `GET /api/media/providers` and a Media section in the PWA.

  Spotify opens its real installed desktop application, and its search hands the application a
  `spotify:` URI, an entry point the installed build registers for on this host. The four web
  services open in Opera. **No unofficial Electron wrapper and no website-repackaging Snap or
  Flatpak is installed or required**, and no package was installed at all.

  The provider catalogue is **code**, not a registry. A client sends an allowlisted provider id and
  at most a bounded search phrase; there is no field anywhere for a URL, a template, a
  query-parameter name, a scheme, or a program, and unknown fields are refused rather than ignored.
  A query is capped at 120 characters, rejected — not stripped — if it carries control characters,
  and percent-encoded by the catalogue into a route the catalogue owns.

  **Nothing claims playback.** Opening Netflix opens a page and searching Spotify opens a search, so
  every media result reports `playback: not_started` on success and the phone repeats that wording
  rather than upgrading it. **TV+ ships without search**: its unqualified search address redirects
  to the storefront root and discards the query, so a search built on it would open the home page
  while reporting success. The card says so, with the reason.

  Opening a provider does not create a runtime instance — a media definition becomes a running
  instance only when discovery finds a real process.

- **Opera is Cofferdam's default browser**, for generic links and for every media web service. This
  is a preference *inside Cofferdam*: the desktop's own default browser and file associations are
  untouched. An explicit profile or browser outranks it, a configured `default_for_url` profile
  outranks it, and a host without Opera behaves exactly as before. A new optional `browser_id` on
  `open_url` selects a browser directly — so "open this in Firefox" no longer needs a registry file
  — and it cannot be used to escape a configured domain allow-list.

- **M2B2 — a user can name their displays, from the phone.** The first write path Cofferdam
  exposes to the network: `PUT` and `DELETE /api/runtime/displays/{resource_id}/overlay`,
  authenticated, `application/json` only, body capped at 8 KiB, unknown fields refused.

  The client addresses a **runtime** resource and sends a label and aliases. It cannot send an
  EDID digest, a registry name, a file path or an overlay id — those are absent from the schema,
  not merely validated away — so a request cannot choose where its label is stored. The server
  takes a fresh snapshot, finds the resource, and derives the persistent key from the panel.

  Only a panel-grade identity may carry a durable name: a host-scoped EDID digest, or a complete
  manufacturer/model/serial. A connector-only display is **refused** rather than stored with a
  warning, because the read side already declines to match on a connector hint — a name kept
  against a socket would move to whatever is plugged in there next. Two displays sharing an
  identity fail closed on both sides.

  Labels never become hardware. The card shows the user's name as its title with the real model
  and connector directly beneath; `resource_id`, connector, manufacturer, model, serial and every
  runtime field are untouched, and the stored entry holds no resolution, position, scale or
  connected flag. Removing the name restores the hardware title.

  Writes are serialized by an advisory `flock` on an adjacent lock file and committed through the
  existing `write_json_atomic`, with the exact document validated by the loader's own parsers
  before it is written and re-read afterwards — so the response is an observation rather than an
  echo, a failed write leaves the previous file byte-identical, and a rejected edit can never
  become a registry that fails to load at next start. `DELETE` is deliberately not idempotent.

  Text follows the registry's existing Turkish-aware rules: `Büyük monitör`, `büyük monitör` and
  `BÜYÜK MONİTÖR` are one alias, `IŞIK` matches `ışık`, composed and decomposed spellings agree,
  and the first spelling the user wrote is the one kept. Overlay writes are audited as bounded
  action records — operation, resource, outcome — deliberately **without** the label text, which
  is the user's own words about their own home and does not belong in a general-purpose log.

  Application-instance labels remain future work: their identity is PID plus start time, which is
  boot-scoped, so a label could not survive the restart that makes one worth having.

- **Runtime inventory (M2B) — Cofferdam can see what is actually connected and running.** The
  layer M2A deliberately did not have. Read-only discovery lives in
  `cofferdam/workstation/runtime/`, one narrow module per backend, each stating the resources it
  owns, the evidence it uses, its limitations and its status semantics. Full write-up in
  [`docs/RUNTIME_INVENTORY.md`](docs/RUNTIME_INVENTORY.md).

  - **Connected displays** — from `org.gnome.Mutter.DisplayConfig.GetCurrentState` (the
    compositor's own view: layout, scale, orientation, refresh rate, primary, `is-builtin`),
    joined to `/sys/class/drm` for the EDID fingerprint and physical millimetres. Deliberately
    **not** `xrandr`: under Wayland it reports XWayland's synthetic layout, and M1 could only ever
    honestly take a display *count* from it. The two sources are joined on the panel's own
    EDID-derived `(manufacturer, model, serial)` triple, because the kernel says `card1-HDMI-A-1`
    where Mutter says `HDMI-1` — content matching is exact, a hand-maintained name mapping is a
    guess. Display identity is the SHA-256 of the EDID scoped to the host, so a label survives a
    reboot and a cable moved to another port; a panel whose EDID cannot be read gets a
    connector-derived identity explicitly marked `weak`. Manufacturer, model and serial are
    reported exactly as the hardware described itself, and a panel that published no model *name*
    is reported by its product code with `model_source` saying so — nothing becomes "Unknown".
  - **Processes** — `/proc`, read directly. Identity is host + boot + PID + start time, never a
    bare PID: PIDs are recycled within minutes, and `start_ticks` is published so a later control
    action can re-verify it before acting. A host with no boot identity gets an `unavailable`
    collection rather than bare PIDs. A process that exits mid-scan is omitted without degrading
    the collection; one that exists but cannot be read downgrades it to `partial`.
    `/proc/<pid>/cmdline` and `/proc/<pid>/environ` are **never opened** — both routinely carry
    secrets, and not reading them is a far easier guarantee than redacting them.
  - **Running application instances** — grouped by systemd cgroup scope, because the system
    already computed the boundary. Opera's **19 processes are one running Opera**, not nineteen;
    a GNOME launch that produces two scopes for one application is one instance, merged on
    systemd's naming grammar rather than by substring. Mapping to an application definition
    requires the exact basename of the root process's real executable — `operator` is not Opera,
    and an Electron application bundling a `chromium` binary does not become Chromium. No match
    leaves the instance running and **unmapped**, which is a complete answer.
  - **Windows** — the interface exists and is wired into the snapshot; **no safe read-only backend
    is available on GNOME Wayland**, so the collection reports `unavailable` with a precise
    reason. `org.gnome.Shell.Eval` returns `(false, '')` on this host and would be arbitrary code
    execution inside the compositor anyway; no portal enumerates windows; the accessibility bridge
    is switched off and enabling it is the user's decision. An empty list would tell a user with
    three windows open that they have none. The seam for a user-installed GNOME companion is
    documented.

  Collection status is a closed vocabulary — `ok` / `partial` / `unavailable` / `error` — and the
  model *enforces* it: an `unavailable` collection that carries items, or omits a reason, raises.
  An `ok` collection with zero items is a positive claim that the machine has none of that
  resource. Recorded as `DECISIONS.md` D-2026-08-05-2, -3 and -4.

- **Authenticated read-only runtime API.** `GET /api/runtime` serves one snapshot —
  `observed_at`, host/boot/session identity, and the four collections — and
  `GET /api/runtime/{resource_kind}` serves one slice of it together with that header, because a
  list of processes is uninterpretable without the boot it was read in. Sub-endpoints slice a
  shared snapshot rather than scanning independently, so a client can never assemble a picture
  whose displays came from one instant and whose processes came from another. A short cache keeps
  a polling phone from driving a continuous process scan, and is invalidated by *identity* as well
  as by time: a replaced graphical session drops it however recent it is. `?refresh=true` bypasses
  it. **No route accepts a write method** — process and window control is a later milestone with
  its own identity re-verification rules.

- **A "Live system" area in the PWA**, in its own `web/live.js`, separate from *Configuration &
  templates*. The separation is structural so each file can be checked for the vocabulary the
  other must never borrow: `app.js` renders definitions and may not say "running", `live.js`
  renders runtime resources and may not say "installed — can launch". This closes the M2A
  live-validation finding that the card reading *Firefox available* was taken to mean Firefox was
  open. An `unavailable` collection renders the backend's reason, and that branch is checked
  *before* the empty branch — an unavailable collection has zero items too. Values the host did
  not report render as "not reported"; window counts are absent rather than zero. Cards are
  compact and expand on tap; polling is conservative, pauses while the page is hidden, and stops
  on sign-out.

- **`HostAdapter.application_executables()`** — a read-only view of the adapter's own launch table,
  so runtime discovery can map a process group to a definition without hardcoding a program name.
  It deliberately does not follow `/snap/bin/opera` to its symlink target `/usr/bin/snap`, which
  would classify every unrelated snap helper as Opera.

### Changed

- **The Live system view is a control plane, not a system monitor (2026-08-05).** Real-client
  validation on the phone confirmed the backend correct — two real displays, Opera and Firefox as
  one instance each, truthful unavailable states — and the *page* wrong. The primary application
  list mixed Opera and Firefox with `evolution-alarm-notify`, `gsd-disk-utility-notify` and
  `update-notifier`; the process section rendered ~116 rows of systemd, D-Bus and PipeWire before
  anything a person controls; expanded display cards opened with serial numbers and EDID
  fingerprints; and Screenshot stayed the most prominent control on a host that truthfully reports
  it unavailable.

  No inventory data was removed and no collection is filtered. Instances now carry `presentation`
  (`user_facing` / `background` / `unclassified`) plus `presentation_evidence`, derived from
  application-definition matches and freedesktop desktop-entry metadata — `NoDisplay`, `Hidden`,
  and XDG autostart membership — never from name substrings and never from a list of the
  applications on this host. Background helpers and undecidable groups keep their full cards in
  collapsed sections; the process inspector is collapsed, builds no rows until opened, and then
  offers search and a per-application filter; display and application technical details move
  behind a second disclosure; an unavailable capability leaves the primary control row for a
  collapsed "Unavailable on this host" area carrying the host's own reason; Windows becomes a
  compact capability row instead of a section-sized empty state.

### Fixed

- **The default audio output was reported as absent on a host that had one.** `pw-dump` publishes
  `Metadata` objects with their properties at the **top level** and no `info` key at all, unlike
  nodes and devices. Reading only `info.props` therefore found no `default.audio.sink`, and a
  machine with a perfectly good speaker was described as having no default output. Found by running
  the code against the real host rather than against fixtures, and the test fixture now reproduces
  both shapes so it cannot regress.

- **A fresh iPhone could never connect, and said nothing about it (2026-08-05).** An onboarded
  tablet worked; a fresh iPhone loaded the PWA shell and stayed on "Connecting…" indefinitely,
  with no token form and no error.

  `web/app.js` began its boot with a bare `localStorage.getItem(...)`. On iOS Safari, storage
  access **throws** rather than returning null under Private Browsing, "Block All Cookies", and
  some lockdown/MDM configurations. Nothing caught it, so the exception escaped the module's
  IIFE and the rest of the script never ran — `#setup` and `#app` both keep `hidden` in the
  served markup and `#connText` ships the literal "connecting…", so the page was displaying its
  own initial HTML. It was not connecting; it was never going to do anything. The tablet was
  unaffected because it had been onboarded earlier and had working storage, so it never reached
  the throwing path.

  Ruled out with evidence rather than assumption: the service worker is network-only and caches
  nothing; `/ws` performs no `Origin` check; the socket scheme is derived from
  `location.protocol`; and the journal records no `/ws` handshake from any address but the
  tablet's, so the phone never reached the WebSocket at all. Separately noted and *not* claimed
  as the cause: bursts of uvicorn "Invalid HTTP request received", consistent with Safari HTTPS
  upgrade probing against an http-only origin.

  Every storage access is now wrapped, with an in-memory fallback so a device whose browser
  refuses storage still works for the session and is told it cannot be remembered. Connection
  state is explicit and exhaustive — `connecting`, `auth_required`, `auth_rejected`,
  `unreachable`, `connected` — with bounded timeouts on both the initial status request (8s) and
  the WebSocket open (10s), a Retry action, and the real reason shown. Only the first attempt may
  display "connecting…"; background reconnects keep the failure state visible, which closes a
  second indefinite-"connecting…" path the new tests found in the reconnect loop. The boot is
  wrapped and a last-resort `error` listener converts any unhandled throw into a real state.
  Server authentication is unchanged: `/ws` still closes 4401 before upgrade, the REST API still
  answers 401, and the token still travels only in a Bearer header or the WebSocket subprotocol —
  never a URL. See [`docs/DEVICE_ONBOARDING.md`](docs/DEVICE_ONBOARDING.md).

- **Launch provenance claimed a fact it could not prove, for every snap application
  (2026-08-05).** Found during PR #13 live validation on the real Ubuntu host. Cofferdam issued
  `open_application` for Firefox; the instance was discovered and grouped correctly, and reported
  `launched_by_cofferdam: false` — about a launch Cofferdam had just performed. Snapd re-parents
  every snap launch out of our `cofferdam-app-<hex>.service` into
  `snap.<package>.<app>-<uuid>.scope` before the first scan, so the evidence is gone by then. A
  boolean has no way to express "cannot be determined", so it asserted the one reading that was
  definitely untrue: that something else had launched it. Opera was equally affected.

  The boolean is replaced by three-valued `launch_source` — `confirmed_cofferdam`,
  `confirmed_external`, `unknown`. Snap scopes report `unknown` unconditionally, and the absence
  of our transient unit is never on its own grounds for `confirmed_external`: that state requires
  a launcher to have named *itself* in the unit (`app-gnome-<AppID>-<pid>.scope`), a shape
  Cofferdam cannot produce because `systemd-run --user --unit=` creates a `.service`. The PWA
  badges only the two confirmed states and renders `unknown` as "launch source not confirmed",
  never as "not launched by Cofferdam". Regression test covers a Cofferdam-started snap moved into
  a snap scope.

- **A live-validation report said Firefox was not installed on this host; it is (2026-08-05).**
  `STATUS.md` recorded "Firefox is not installed on this host and correctly produces no instance."
  Firefox is installed and launchable — snap 149.0.2-1, resolved at `/usr/bin/firefox` from the
  daemon's own `PATH`, and already listed by `/api/status` as an available application in the same
  document. It was merely not *running*. The sentence reproduced in prose exactly the
  installed-versus-running conflation this milestone exists to remove. Corrected against live
  evidence: launching Firefox through Cofferdam produced one `firefox` instance, 11 processes
  grouped under one card, matched by executable basename. No discovery-code defect was involved.

- **Screenshot capability was over-advertised in a Wayland session (2026-08-05).** After login,
  a daemon started at boot by lingering reported `screenshot: true` on a GNOME Wayland host
  because `scrot` was on `PATH`, and the phone enabled a Screenshot button whose action could
  only fail (`scrot: Can't open X display`). The guard that rejects X11 root-capture tools under
  Wayland was reading `XDG_SESSION_TYPE` from the **service's own** environment, which under
  lingering is empty — GNOME populates the user *manager* at login, not an already-running
  process — so the guard silently never applied. The action failed closed throughout (bounded
  `adapter_failed`, no black image, no false success), so this was an advertisement-accuracy
  defect, not a capture-correctness one. Capability is now derived from the verified graphical
  session returned by `detect_graphical_session()`, which also carries the session's live
  environment; a capture runs with that session's display variables, and a stale
  `DISPLAY`/`WAYLAND_DISPLAY` inherited from an ended session is dropped rather than passed on.
  A session publishing `WAYLAND_DISPLAY` counts as Wayland even without `XDG_SESSION_TYPE`.
  **No Wayland screenshot backend was added** — Wayland capture remains unavailable on this
  host and the flag now says so truthfully. Recorded as `DECISIONS.md` D-2026-08-05-1.
- **Ubuntu graphical login loop caused by the workstation service (2026-08-04).** Enabling
  `cofferdam-workstation.service` made GNOME unable to complete a login: the password was
  accepted, the desktop began to load, and the session died back to GDM — every time. The unit
  declared `Wants=graphical-session.target` while being `WantedBy=default.target` on a host with
  `loginctl enable-linger`. Lingering starts the user manager at boot; `default.target` pulled
  the service in; and `Wants=` **activated** `graphical-session.target` with no compositor behind
  it. gnome-session then found the target it is itself supposed to activate already active,
  refused with "A graphical session is already running!", and quit. Confirmed against the journal
  across four failing boots versus one working control boot. The unit no longer references
  `graphical-session.target` in any form; session detection was already a read-only query and
  stays one. Recorded as `DECISIONS.md` D-2026-08-04-1. Full analysis, migration, rollback, and
  TTY recovery in [`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md).
- **Restart storm when the Tailscale address was not up yet (2026-08-04).** The daemon binds
  directly to its private address, which frequently does not exist yet when lingering starts it
  at boot; the bind failed, the process exited, and `StartLimitIntervalSec=0` disabled the rate
  limiter, so it respawned every 3s indefinitely. It now waits for the address, bounded by
  `COFFERDAM_BIND_WAIT_SECONDS` (default 120), then exits cleanly. The unit's restart policy is
  bounded (10 attempts / 5 minutes). The service still never falls back to a wildcard bind.

### Added

- **M2A — control plane foundation (2026-08-04).** Cofferdam gains a vocabulary for the machines,
  displays, applications, browser profiles, agents, and routes it is allowed to talk about.
  - **Six versioned JSON registries** under `$COFFERDAM_HOME/config/registries/` — `devices`,
    `displays`, `applications`, `browser_profiles`, `agent_profiles`, `conversation_routes` —
    with strict typed models, cross-registry reference validation, stable ASCII kebab-case IDs,
    normalized Unicode alias indexes, safe empty defaults, an atomic writer utility, and bounded
    structured errors. Machine registries are never committed; committed placeholders live in
    `examples/registries/`. Standard-library only: no database, no YAML, no new dependency.
  - **Alias resolution** folds Unicode case, trims and collapses whitespace, and folds Turkish
    dotted and dotless I together, so "MONİTÖR"/"monitör" and "IŞIK"/"ışık" match. Duplicate
    normalized names or aliases inside one registry are a validation failure, and the resolver
    returns no match rather than choosing between candidates.
  - **Read-only registry API:** `GET /api/registries` (per-registry version, counts, load status)
    and `GET /api/registries/{registry_name}`, behind the same device token as every other
    state-revealing route. There is no `POST`/`PUT`/`PATCH`/`DELETE` registry endpoint in M2A.
  - **`open_url` gained an optional `browser_profile_id`.** An explicit profile selects its
    application and never falls back to another; domain policy is enforced before launch; an
    unavailable browser reports `application_unavailable`. With no profile given, the single
    enabled `default_for_url` profile is used when its browser is available, otherwise the
    pre-M2A legacy launch is preserved exactly. A URL-only request on a machine with no
    registries behaves exactly as it did before.
  - **Opera** joined the code-owned application allowlist, detected through bounded executable
    names (`opera`, `opera-stable`) and desktop-entry basenames. No executable path, argv,
    command, desktop-file path, profile directory, or credential is representable in any schema.
  - **PWA:** read-only cards for all six registries with loading/empty/invalid/unavailable
    states, agent profiles labelled "not implemented", conversation routes labelled "template
    only", and a browser-profile selector on Open URL. No Start/Send/Run/Route control exists.
  - **Docs:** `docs/CONTROL_PLANE.md`, `docs/DEVICE_REGISTRY.md`, `docs/APPLICATION_PROFILES.md`,
    `docs/AGENT_ROUTING.md`, and `docs/DESKTOP_APP.md` (an ADR comparing an installed PWA, a
    Tauri 2 thin shell, and Electron — recommending a thin Tauri companion, with no scaffolding
    added in M2A). Decisions recorded as `DECISIONS.md` D-2026-08-04-3..5.
  - **Registries are overlays, not runtime discovery.** They were first written the wrong way
    round: the committed examples shipped a `large-monitor` named "Büyük monitör", a
    `personal-opera`, and a `fallback-firefox`, and the PWA presented them as "Machine
    registries". Nothing had been discovered — those were labels for resources no code had ever
    looked for, and a browser profile read as though it meant an open browser. The product now
    separates **definitions** (code-owned: which applications exist as a concept), **runtime
    resources** (connected displays, running processes, application instances, windows —
    **not implemented**, milestone M2B), and **user overlays** (optional labels, aliases,
    preferences: all a registry file is). Consequences: every committed overlay example id and
    name begins with `example`; application definitions keep neutral concept ids (`opera`,
    `firefox`) because they name real code-owned concepts; no code path, shipped script, or
    first-run step copies examples into `$COFFERDAM_HOME`; a machine with no registry files is
    fully working; and the PWA panel became "Configuration & templates", with per-section titles
    naming each layer and an empty state reading "Nothing configured — this is normal, and
    everything still works". Recorded as `DECISIONS.md` D-2026-08-04-6, with D-2026-08-04-7
    adding the semantic-interfaces-only rule — no pixel-coordinate automation, and Cursor as a
    future *target-agent adapter* rather than a route into an existing ChatGPT conversation.
    Pinned by `tests/test_registry_layer_semantics.py`.

  M2A implements no runtime discovery of any kind, and no Raspberry Pi control, Wake-on-LAN or
  power action, window movement, browser
  DOM access, web automation, browser extension, agent execution, message sending,
  natural-language planning, or desktop application scaffolding — and changes no reboot
  behaviour. **M1's post-reboot validation gate remains open.**
- **Service lifecycle documentation and enforcement (2026-08-04):**
  [`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md) separates directly observed facts from
  supported interpretation and unproven assumptions, and documents daemon behaviour before,
  during, and after login, at logout, and across repeated logins.
  `deploy/install-workstation-service.sh` performs a transactional, idempotent migration
  (inventory → back up Cofferdam-owned files → disable the old enablement path → install →
  verify → enable) and refuses to enable a unit that names `graphical-session.target`.
  `deploy/uninstall-workstation-service.sh` is the rollback and TTY-recovery path; it resolves
  every symlink before unlinking, so it can only ever remove its own.
  `tests/test_service_unit_lifecycle.py` fails if any unit pulls, starts, or stops the graphical
  target; if a prohibited session-termination command or a broad `pkill`/`killall` appears; if a
  restart policy is unbounded; if a unit embeds a secret or a wildcard bind; or if an installer
  touches unrelated user configuration.
- **Session identity carried from detection through to launch (2026-08-04):** GUI actions record
  the graphical session generation they were authorised against, and are refused if the session
  ended or changed before the application starts — so a request can never be delivered into a
  different session after a logout/login.

- **Open-source readiness (docs only, 2026-08-01):** `CONTRIBUTING.md` (development setup,
  worktree workflow, action/adapter proposal rules, platform-evidence expectations, review
  depth, and the dependency policy), minimal GitHub issue templates (bug, Ubuntu validation
  report, adapter/action proposal), and a pull-request template. A license and provenance audit
  confirmed Apache-2.0 is unambiguous across `LICENSE`, package metadata, and CI; that nothing is
  vendored; and that no upstream code is present — recorded as `DECISIONS.md` D-2026-08-01-8.
  `.gitignore` hardened against runtime secrets, screenshots, browser profiles, and repository
  bundles. `SECURITY.md` gained a maturity statement and the M1 workstation posture.

### Changed

- **Direction pivot (docs only, 2026-08-01):** Cofferdam is now an open-source, personal,
  always-on AI workstation and remote computer-control system for Ubuntu Desktop, controlled
  from phone/tablet via a Cofferdam-owned PWA, with a Guardian-supervised A/B self-update
  loop. The Trust Core is preserved off the critical path for future privileged-action use.
  New `DECISIONS.md`, `STATUS.md`, `ROADMAP.md`, `AGENTS.md`, `CLAUDE.md`; rewritten
  `README.md`/`DESIGN.md`; scope notes added to the Trust Core docs. No code changes.

### Fixed

- **Opening a URL in an already-running Opera was reported as a failure** (M2A Ubuntu
  validation, snap-packaged Opera 133). Launching `opera <url>` while Opera is running prints
  "Opening in existing browser session.", opens the tab, and exits **24** — Chromium's
  `CHROME_RESULT_CODE_NORMAL_EXIT_PROCESS_NOTIFIED`. systemd marks any non-zero exit as `failed`,
  so the adapter called a tab that had visibly opened "the application exited immediately instead
  of starting". The launcher now accepts a **per-application list of specific** delegation exit
  codes (`{"opera": (24,)}`), and such an exit is still reported as `exited` — never as running.
  The M1 rule is unchanged and still enforced: an exit code alone is never evidence, so the launch
  only succeeds when a live instance of the same application can also be seen. Every other exit
  status, and every other application, still fails closed.
- **Graphical actions were reported as succeeded while nothing opened** (M1 Ubuntu validation,
  GNOME/Wayland, Ubuntu 26.04). `open_application` and `open_url` returned `succeeded` with a PID,
  but no window ever appeared. Two independent defects combined. First, the service runs with
  `NoNewPrivileges=yes`, which drops file capabilities across `execve` for every process it forks;
  Ubuntu's Firefox is a snap whose `snap-confine` needs permitted capabilities, so every launch died
  instantly with `snap-confine is packaged without necessary permissions`. Second, the adapter
  spawned the child and returned its PID without ever waiting, so that failure was invisible —
  and `xdg-open` hid it a second way, exiting 0 after delegating whether or not a browser ever
  started. The adapter now hands each application to the **systemd user manager** as a transient
  unit (`systemd-run --user`), which is not subject to the service's `NoNewPrivileges`, gives the
  application its own cgroup (restarting Cofferdam no longer kills the user's browser), and lets it
  inherit the manager's *current* session environment — so a service started by lingering before
  graphical login still launches into the session created later. Every launch is now confirmed
  before it is reported: the process must survive a settle window, or an existing instance of the
  same application must be visible; otherwise the action fails closed with a structured error.
  `open_url` launches an allowlisted browser directly instead of `xdg-open`, because `xdg-open`
  yields no verifiable outcome. The service's hardening is unchanged, and the fixed-argv boundary
  is unchanged (no caller text ever becomes a command).
- **Status now reports what this host can currently do.** `/api/status` capabilities
  (`screenshot`, `open_application`, `open_url`) are gated on a live check that an active
  graphical session exists — `graphical-session.target` plus a compositor/X socket that really
  exists — rather than on the service's own start-time environment, which is stale or empty on a
  lingering host. `session_type` comes from the same live source. GUI actions fail closed with
  `adapter_unsupported` when there is no session, and the PWA disables every control whose
  capability is false.
- **Corrected the Wayland guidance in the UI and docs.** The status note told users to "log in
  with 'Ubuntu on Xorg' for full support" — misleading, since Wayland runs application and URL
  launches correctly and no Xorg path was validated for screen capture. The note now states only
  what was observed: screen capture is unavailable in this session, launching is unaffected. The
  host runbook and the M1 checklist no longer instruct an Xorg login. Also moved
  `StartLimitIntervalSec` into `[Unit]`, where systemd actually reads it.
- **`FilesystemRepoView` now enforces its documented root containment** (PR02a). Previously the view
  joined caller components to the root and stat/opened the result directly, so a **direct** call with
  hostile parts (a `..` traversal, an absolute/drive/UNC/device component that replaces the root under
  `pathlib` join, or an intermediate symlink) could disclose out-of-root metadata via `path_type` or
  read out-of-root bytes via `read_bytes` — while the class docstring claimed escapes were reported
  fail-closed. (Supported proposal/guard/dry-run flows were **not** affected: `normalize_target`
  rejects such input before any view call and `canonicalize_target` re-checks real-path containment;
  no supported bypass existed.) The view now validates every component lexically before any filesystem
  access, fails closed on any intermediate symlink/reparse component, and checks that the resolved
  parent stays beneath the canonical root: an escape yields `PathType.MISSING` / `RepoReadError` with
  no outside path in the message, while a *final*-component symlink is still reported as `SYMLINK` and
  never followed. Containment is check-then-use (the local-account TOCTOU residual is documented; a
  descriptor-relative `openat`/`O_NOFOLLOW` traversal is deferred to PR4). The previously
  Windows-only-passing escape test is rewritten to be host-independent, and a full containment matrix
  is added. Also adds a minimal Ubuntu (`python 3.12`) GitHub Actions test workflow so the suite runs
  on POSIX in CI.

### Added

- Foundation docs (PR0): `LICENSE`, `README.md`, `PROVENANCE.md`, `SAFETY-AND-RISK.md`,
  `SECURITY.md`, `TESTING.md`, `DESIGN.md`, `THREAT-MODEL.md`, `AUTHORS.md`, `.gitignore`, and a
  license-scan CI check. No product code yet.
- CLI skeleton (PR1): the `cofferdam` package with `--version`, help output, an empty command
  dispatch registry, the exit-code convention, and the stdout/stderr split. Standard-library only;
  no guard, executor, approval, audit, provider, or network behaviour yet.
- Trust-core foundation (PR2a): a strict fail-closed proposal schema/parser (`proposal.py`), path
  normalization + containment + protected-path matching (`paths.py`, `protected_paths.py`), a
  read-only injected repo view for symlink/type checks (`repo_view.py`), and the shared verdict
  vocabulary with a no-`ALLOWED` decision set (`verdict.py`). Finalized the PR2a-relevant sections of
  `THREAT-MODEL.md`. Standard-library only; no network, no subprocess, no file mutation. The guard
  decision engine and diff validator remain PR2b; approval/executor/audit remain PR3.
- Deterministic guard and diff validator (PR2b): `guard.py` with the frozen
  `evaluate(proposal, repo_view)` signature and an architectural fail-closed wrapper; `diffcheck.py`,
  a positive-grammar validator for the narrow git unified-diff subset (newline normalization, hunk
  line-count checking, and strict `---`/`+++` path matching against `target_path`); and the immutable
  `Verdict` container with byte-stable canonical serialization. Malformed, multi-file, binary,
  truncated, oversized, and path-mismatched diffs all fail closed. Still `BLOCKED`/`NEEDS_APPROVAL`
  only — no `ALLOWED`. Standard-library only; no network, no subprocess, no file mutation.
  Approval/executor/audit remain PR3.
- Trust-core binding foundation (PR3a — **non-mutating**): domain-separated, length-prefixed SHA-256
  hashing utilities with frozen constants and known test vectors (`hashing.py`); read-only canonical
  target resolution that rejects symlink/reparse components, root escapes, and non-regular targets
  (`canonicalize.py`); a deterministic pre-state descriptor distinguishing absent / empty-regular /
  non-empty-regular (`prestate.py`); a bounded, symlink-rejecting read-only `read_bytes` on the repo
  view (`repo_view.py`); and an **immutable dry-run artifact** (`dryrun.py`). The artifact derives
  its patch bytes **internally** from the validated proposal (`proposal.diff.encode("utf-8")`) — one
  authoritative artifact, no independent caller-supplied patch — and takes its repository root
  **only** from the repo view (the view canonicalizes/validates its root and owns
  `root_real_path()`/`root_bytes()`), so path/root/pre-state cannot diverge across two roots. The
  binding hash uses `TAG_BOUND = "cofferdam.binding.v1"` and proves **binding, not authorization**.
  Standard library only; no file writes, no subprocess, no network. **No approval, nonce, ledger,
  expiry, TTY, `git apply`, executor, or audit exists yet** — those are PR3b/PR3c/PR3d.
- Approval-state layer (PR3b — **non-executing**): a durable, expiring, single-use approval ledger
  under the repository's own `.cofferdam/` workspace, with the strict approval/consumption record
  schemas and canonical JSONL serialization (`approval.py`), a deterministic fail-closed fold scoped
  by `repo_root_id`, the append-only store with a cross-process advisory lock, `fstat`-after-open
  permission/owner checks, symlink/reparse + non-directory rejection, fail-closed handling of
  torn/malformed records (**a non-empty ledger that does not end in a complete LF-terminated record
  invalidates the whole ledger — no automatic repair**, so a torn consumption line can never
  resurrect a consumed approval), consumption↔approval `bound_hash` cross-checking, size caps, and a
  write-all→fsync append protocol (`approval_store.py`), an injectable wall-clock abstraction
  (`clock.py`), and a **read-only** `cofferdam approval-status` command that creates no state.
  **Ledger integrity is the authorization property**: `approval_id` is a random event identifier,
  never a bearer token, and `bound_hash` alone never authorizes. The supported public functions
  `find_valid_approval(bound_hash, repo_view)` / `consume_approval(bound_hash, repo_view)` take **no**
  caller-injectable clock/store/lock/path/TTL (dependency injection exists only on unexported
  internal seams for tests and future PR3c wiring); the store itself is internal-only
  (`_ApprovalStore`, no public append API). Single-use is regression-tested across two real OS
  processes. PR3b writes only its own `.cofferdam/` state files. Standard library only; no repository
  mutation, no `git apply`, no subprocess, no network, no audit. **Deliberately absent (deferred):
  the human-mediated approval mint and TTY confirmer (PR3c1), the byte-exact executor (PR3c2), and
  the hash-chained audit log (PR3d)** — there is no production path that *creates* an approval in this
  release. `prestate.py` now raises an explicit error (instead of `assert`) so its content-hash
  invariant survives `python -O`.
- Interactive human approval mint (PR3c1): the **`cofferdam approve --file <proposal.json>`** command
  (`approve_cli.py`) — the **only supported path that creates authoritative approval state**. It
  rebuilds the exact dry-run artifact from `(Proposal, RepoView)`, screens the target and patch for
  terminal-unsafe characters (rejecting CR, C0/C1 controls, DEL, ANSI escapes, bidi-formatting,
  zero-width, Unicode noncharacters, and surrogates — such proposals are unapprovable in v0.1),
  displays the **complete patch through one reversible, injective, ASCII-only escape grammar** — a
  literal backslash as `\\`, a TAB as `\t`, each trailing space as `\x20`, every non-ASCII code point
  as `\u{HEX}`, and a header field stating whether the patch ends with a final `LF` — so two different
  patches can never render identically (the bytes bound are the original `proposal.diff` UTF-8, never
  the rendered text). It shows the full 64-hex `bound_hash` and — only when stdin, stdout, and stderr
  are all TTYs — requires the human to type `APPROVE <first 12 hex of bound_hash>` exactly, once (the
  confirmation line is capped at **256 UTF-8 bytes**). On success it **rebuilds the artifact a second
  time under the ledger lock**, requires the full 64-hex `bound_hash` to match what was displayed (so a
  repository change during confirmation fails the approval), then appends one record with a
  `secrets.token_hex(32)` `approval_id`, `created_at` from `SystemClock`, and a fixed 300-second TTL,
  and fsyncs it. Exit codes: `0` recorded, `1` declined/mismatch/EOF/interrupt/already-active, `2`
  usage/non-TTY/input/guard/render/state-change/ledger error. Terminal writes go through a helper that
  turns an encoding/stream failure into a bounded, terminal-safe error with **no traceback**: the
  complete change and prompt must be **written and flushed** (a checked `flush` runs immediately before
  the confirmation is read, so a fully buffered stream whose flush fails aborts **before** any mint),
  and untrusted paths and raw exception strings are never echoed. A post-mint success-message write or
  flush failure keeps the indeterminate-authority posture (the approval already exists). If the record is written completely but its `fsync` then
  fails (or the record is flushed but the success message cannot be shown), the command exits `2` with
  a bounded **"approval state is indeterminate"** warning that points at `cofferdam approval-status`,
  rather than falsely reporting that no approval exists (`LedgerDurabilityError`, an `OSError`
  subclass; Cofferdam never auto-truncates or rolls back). There is **no** public
  `create_approval`/`mint_approval` Python API and **no** non-interactive path (`--yes`/`--force`/
  `--repo`/`-`/stdin-proposal/config/env are all absent); the internal mint seam takes no
  caller-controlled clock/store/entropy/TTL/`approval_id`/`bound_hash`. `approve` **executes nothing**:
  no `git apply`, subprocess, Git invocation, staging, committing, or proposal-target mutation — it
  writes only its own `.cofferdam/` state. First-ever concurrent state creation is hardened
  (`_ensure_dir`/`_ensure_lockfile` catch and re-validate a lost `FileExistsError` race) and covered by
  a real two-process regression, as is concurrent minting (exactly one of two racing processes
  succeeds). **Deliberately still absent (deferred): the byte-exact executor (PR3c2) and the
  hash-chained audit log (PR3d).**
