# DailyGrad LeetCode coaching

Read [the main skill](../SKILL.md) first. DailyGrad owns exercise selection,
tracks, preferences, and progress. Tari helps the user reason about the selected
problem and relays their explicit completion or skip request.

## Establish which exercise is current

Read the exercise from `dailygrad_digest`; use the named date/run for an older
exercise. If it has none, say so. Do not choose a replacement or reconstruct
catalog text from memory.

One exercise can appear across several days until the user ends it.
`is_carryover` and `day` describe repeated appearances of that same question;
they do not make it a new problem or a review. The authority for the exercise
current now is `dailygrad_leetcode({action:"status"})`: `active_exercise_id`,
`exercise`, and `completion_pending`.

After `next`, its returned `active` exercise is current immediately while the
latest digest still contains the previous exercise. Compare exercise IDs, or
problem IDs if an older digest lacks an exercise ID. Read status before coaching
when an exercise was ended earlier in the conversation or its identity is unclear.

An exercise with `days_shown:0` has not appeared in a digest. Do not describe it
as carried over or count days yet. Its full statement comes from the `next`
result. If that result is unavailable, status still provides its identity/link;
explain that the full exercise will arrive in the next digest rather than
inventing its wording. An older digest's hint and reference solution belong to
the older exercise and must never be applied to the new one.

## Coach without giving away the answer

Respond to the user's actual approach: explain whether the data structure and
algorithm fit, assess time and space complexity for that approach, and identify
an overlooked edge case. Use your own reasoning; tutoring sends nothing back to
DailyGrad. Ask at most one follow-up question when it helps the discussion.

Let the user reason first. Give hints in small steps when asked; explain the full
conceptual solution only on request, and provide pseudocode or code when the
user asks for it. A requested conversational hint is fine even when automatic
hints are off. `reference_solution` may be partial, null, or absent; use it when
available, and distinguish your own reasoning where it supplies no answer.

Automatic hints are a separate persistent preference. Read it with `status`;
change it with `hints_on` or `hints_off` only when requested. Report the returned
`hints_enabled` and `changed` values. The setting applies to later runs and does
not regenerate the current digest.

## Let the user end the exercise

Read `status` in the same turn before either mutation. Use the returned
`active_exercise_id` as `exercise_id`, never a remembered ID, problem number, or
ID inferred from an older digest.

- “Mark complete” or “I'm finished” uses `complete`. This records completion;
  normally the next digest assigns the next exercise.
- “Next question” or “skip” uses `next`. It normally records a skip and returns
  the next exercise immediately. Present its supplied title, statement, example,
  constraints, and questions to think about without adding an unsolicited solution.
- “Finished; give me the next one now” authorizes `complete`, then `next` for
  that same exercise ID. A completed exercise remains completed rather than skipped.

If no exercise is open (`completion_pending:false`) and the user requests the
next one, use `exercise.exercise_id` from status. Act on a clear request without
requiring an attempt, a correct answer, a reason, or another confirmation. Ask
only when it is unclear which exercise they mean.

A good explanation, asking for help, or requesting a solution does not authorize
completion or skipping. After a discussion, a brief offer to mark complete may
be useful; act only if the user accepts. Completion says the user is finished
with the exercise, not that they solved it in code. Other progress marks are not
supported by these tools.

Report what the result confirms. `changed:false` means it was already so.
`stale_exercise` requires a fresh status read; do not apply the old instruction
to a newly selected exercise automatically. Capability errors and uncertain
outcomes are not successful changes. Ending an exercise does not run DailyGrad,
rewrite its existing digest, or enable a schedule.
