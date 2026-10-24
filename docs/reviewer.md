# The Reviewer - the teacher of the Tunnel Doctor

The owner (2026-10-24): "I teach the Doctor everything by hand - can the AI be connected to the panel so that I need not tell it each time, and teach the Doctor too?"

The Reviewer is the first step (1.69.135): **it reads, it never acts.** After every smart repair that did not heal the tunnel, took ten minutes or more, or has something the panel's own rules can find, it writes a review
on the **Reviewer** tab of the Tunnel Doctor (`services/reviewer.py`, `web/review.js`, `/api/reviewer`).

## What it writes

1. **Findings** - the panel's own rules, no AI needed: the repair began (or went on for two minutes) while a server of the tunnel was off; it took the tunnel off its servers and could not build it again; the same error ended
   four builds or more; eight tunnel types or more were tried one after the other; it took ten minutes or more; the tunnel was repaired without success three times in a day.
2. **A review** - when "Let the AI write the reviews" is on and a key is saved in Settings → Api & Key: a verdict (good / slow / wasteful / wrong / unlucky), two plain sentences, and what the Doctor should have done. At most six
   calls an hour, one per tunnel in half an hour (a failed call counts). The AI gets the story of the repair, the steps, when each server went off or came back, what the Doctor knows about the pair and the repairs of the last
   days, as **data** - with server and tunnel names but **no IP addresses**.
3. **A report** for the developer - what the Doctor did, what it should have done, the findings, the evidence: "Copy report" on the review. This is how a mistake of the Doctor gets to be fixed in the code without anyone
   having to find it in a log.
4. **Lessons** - only the AI proposes them, only from a closed list, and **nothing is in force before the owner approves it**:
   * `skip_step` - do not try `rebuild` or `relink` on this tunnel for 1-14 days (never `restart`: a dead service is always restarted first; never `switch` or `move`: they are the way out of a cut);
   * `avoid_kind` - do not try this tunnel type between this pair of servers for 1-14 days (never all of them: a cut tunnel must always have something to try);
   * `note` - a rule the Doctor does not have yet, for its developer (changes nothing).
   An approved lesson reaches the Doctor through the two places it already asks for advice: `analyst.hints_for` (the steps of a smart repair; the story says "Lesson #n (you approved it)") and `knowledge.plan_for` (the types
   it tries). It ends by itself; "Revoke" ends it at once.

## Safety

* The AI has no shell and no action of its own. Its answer is parsed into a fixed shape and every field is checked (`parse_review`); a lesson of a kind, a step or a type that is not in the list is thrown away.
* The log lines it reads can hold text written by third parties: the prompt says they are data, and nothing in the answer is ever run.
* A review runs in its own thread after the repair has ended: it cannot hold a repair back or break one. A read-only panel (the public demo) reviews nothing and cannot change anything here.
* The same lesson is not proposed twice (more evidence is added to it); one the owner dismissed is not proposed again for a week.

## Next steps (not built)

The decider (the AI picks the next step of a stuck repair from the closed list, behind the same guards) and a daily summary. Neither exists yet.