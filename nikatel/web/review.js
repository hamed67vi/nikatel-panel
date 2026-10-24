/* The Reviewer tab of the Tunnel Doctor (services/reviewer.py): what the Doctor's teacher found about the repairs that went badly - the panel's own findings, the AI's review, a report to copy for the developer - and the lessons it
   proposes for the Doctor (nothing is in force before you approve it), read from /api/reviewer. Loaded after knowledge.js and uses the helpers of app.js ($, api, esc, toast, ago, when, showModal, storyHtml). */

const RV_SEV = {bad: ["err", "serious"], warn: ["warn", "look at it"], note: ["", "note"], good: ["ok", "fine"]};
const RV_VERDICT = {good: "acted well", slow: "too slow", wasteful: "wasted work", wrong: "wrong move", unlucky: "unlucky"};
const RV_STATE = {proposed: ["warn", "proposed"], approved: ["ok", "in force"], dismissed: ["", "dismissed"], expired: ["", "ended"]};

window.rvLoad = async () => {
  const box = $("#rv-box"); if (!box) return;
  let r; try { r = await api("/api/reviewer"); } catch (e) { if (!state.rv) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const was = state.rv ? JSON.stringify(state.rv) : "";
  state.rv = r;
  if (was && was === JSON.stringify(r) && box.querySelector(".rv-bar")) return;          // nothing changed: nothing is redrawn (a redraw is what makes a page jump)
  rvDraw();
};

/* the page is drawn again every 15 s: the box is filled from what was read last time at once, then read again */
window.rvHtml = () => state.rv ? rvMarkup(state.rv) : null;

function rvDraw() {
  const box = $("#rv-box"); if (!box || !state.rv) return;
  const sc = box.closest(".ap-tbody"), top = sc ? sc.scrollTop : 0;
  box.innerHTML = rvMarkup(state.rv);
  if (sc) sc.scrollTop = top;
}

function rvMarkup(r) {
  const a = r.ai, ro = !!r.read_only;
  const bar = `<div class="kn-bar rv-bar"><label class="chk" title="On: when a repair went badly or took long, the AI you chose in Settings → Api &amp; Key reads what happened and writes a review (at most ${a.per_hour} an hour, one per tunnel in half an hour). It gets the story of the repair with server and tunnel names but no IP addresses. It never touches a tunnel or a server."><input type="checkbox" ${a.on ? "checked" : ""} ${ro ? "disabled" : ""} onchange="rvSet({ai: this.checked})"> Let the AI write the reviews</label>
    <span class="pill ${a.on && a.ready ? "ok" : "warn"}" title="${esc(a.ready ? "The AI that is chosen in Settings → Api & Key" : "Save a key in Settings → Api & Key: until then the reviews hold only what the panel's own rules found")}">${a.ready ? "AI ready · " + esc(a.who) : "no AI key - the findings still work"}</span><span class="sp"></span>
    <small class="mut">${r.stats.reviews} review${r.stats.reviews === 1 ? "" : "s"} · ${r.stats.bad} serious</small>
    <button class="sm" ${ro ? "disabled" : ""} onclick="rvRun()" title="Review the newest finished repair that has no review yet - even one that went well">Review the last repair</button></div>`;
  const btn = (id, status, label, pri) => `<button class="sm ${pri ? "pri" : ""}" ${ro ? "disabled" : ""} onclick="rvLesson(${id}, '${status}')">${label}</button>`;
  const lesson = x => { const [tone, word] = RV_STATE[x.state] || ["", x.state];
    return `<div class="rv-less ${x.state}"><span class="pill ${tone}">${word}</span><b title="${esc(x.text)}">${esc(x.text)}</b>${x.state === "approved" && x.days_left ? `<small class="mut">${x.days_left} more day${x.days_left === 1 ? "" : "s"}</small>` : ""}<span class="sp"></span>${x.state === "proposed" ? btn(x.id, "approved", x.kind === "note" ? "Noted" : "Approve", true) + btn(x.id, "dismissed", "Dismiss") : x.state === "approved" && x.kind !== "note" ? btn(x.id, "dismissed", "Revoke") : ""}<span class="rv-why mut" title="${esc(x.why)}">${esc(x.why)} <span class="mut">· from ${(x.evidence || []).length} review${(x.evidence || []).length === 1 ? "" : "s"}</span></span></div>`; };
  const review = x => { const [tone, word] = RV_SEV[x.severity] || ["", x.severity], m = Math.floor(x.secs / 60);
    return `<div class="rv-row ${x.severity}"><div class="rv-h"><time class="mut" title="${when(x.at)}">${ago(x.at)} ago</time><b>${esc(x.tunnel)}</b><span class="mut">${esc(x.iran)} ⇄ ${esc(x.foreign)}</span><span class="pill ${tone}">${word}</span>${x.verdict ? `<span class="pill rv-v v-${esc(x.verdict)}" title="The AI's verdict on the Doctor">${esc(RV_VERDICT[x.verdict] || x.verdict)}</span>` : ""}<span class="mut">${x.ok ? "healed" : "not healed"} · ${m ? m + " min " : ""}${x.secs % 60} s</span><span class="sp"></span>
        <button class="sm" onclick="rvStory(${x.id})" title="What the Doctor said and did, line by line">Story</button><button class="sm" onclick="rvReport(${x.id})" title="Copy a report for the developer: what the Doctor did, the findings and the evidence">Copy report</button></div>
      <div class="rv-s">${esc(x.summary)}</div>${x.mistake ? `<div class="rv-m"><b>It should have:</b> ${esc(x.mistake)}</div>` : ""}
      ${(x.findings || []).length ? `<ul class="rv-fl">${x.findings.map(f => `<li class="${esc(f.sev)}">${esc(f.text)}</li>`).join("")}</ul>` : ""}${x.ai_note ? `<small class="mut rv-note">${esc(x.ai_note)}</small>` : ""}${x.lesson_id ? `<small class="mut rv-note">A lesson was proposed (#${x.lesson_id}) - see above.</small>` : ""}</div>`; };
  return `${bar}
    ${r.lessons.length ? `<h4 class="kn-h">Lessons for the Doctor${r.stats.proposed ? ` · ${r.stats.proposed} waiting for you` : ""}</h4><div class="rv-list">${r.lessons.map(lesson).join("")}</div>` : ""}
    <h4 class="kn-h">Repairs that went badly or took long</h4><div class="rv-list">${r.reviews.map(review).join("") || '<div class="dr-empty"><b>No review yet</b><p>After a repair that did not heal the tunnel, took long, or worked on a server that was off, the Reviewer writes here what went wrong - and, with an AI key, whether the Doctor acted well and what it could learn.</p></div>'}</div>`;
}

window.rvSet = async body => { try { await api("/api/reviewer", {method: "PUT", body: JSON.stringify(body)}); toast("✓ Saved", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } rvLoad(); };
window.rvRun = async () => {
  try { await api("/api/reviewer/run", {method: "POST"}); toast("✓ The review is being written (the AI may take a minute)", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  [4000, 15000, 45000].forEach(ms => setTimeout(() => { if ($("#rv-box")) rvLoad(); }, ms));
};
window.rvLesson = async (id, status) => {
  try { await api(`/api/reviewer/lessons/${id}/${status}`, {method: "POST"}); toast(status === "approved" ? "✓ The Doctor follows it from the next repair" : "✓ Done", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  rvLoad();
};
window.rvReport = async id => {
  let text; try { text = (await api(`/api/reviewer/reviews/${id}/report`)).report; } catch (e) { toast("✕ " + e.message, "bad"); return; }
  try { await navigator.clipboard.writeText(text); toast("✓ Report copied - paste it where you tell the developer", "ok"); }
  catch (e) { showModal(`<b>Report of repair #${id}</b>`, `<textarea class="rv-report" readonly rows="16" onfocus="this.select()">${esc(text)}</textarea><small class="mut">The browser did not allow the copy: select it and copy by hand.</small>`, "wide"); }
};
window.rvStory = async id => {
  try { const a = await api("/api/actions/" + id); showModal(`<b>Story of repair #${id}</b> <span class="mut">${esc(a.ref || "")}</span>`, `<div class="fix">${storyHtml(a)}</div>`, "wide"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
