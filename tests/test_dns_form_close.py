"""The record form of the CDN page closes at once when Save is pressed (the owner, 2026-10-23, with a picture of the Edit record box on its Save: "when we set a DNS or change something here, after Save the box must close").
The provider answers in its own time (ArvanCloud: read the record, write it, read it back; Cloudflare the same, a second or two for each): the box used to stay open, its Save dimmed, until the whole answer came.
Now it closes in the same moment, a line of the status bar says "Saving the record…", and the answer is "✓ Record changed" / "✓ Record made" or - when the provider refuses - the form opens again with what was typed
and the reason. Measured in the browser on a test panel with fake providers that took 3 s per call: the box was gone at 0 ms (before: after 12 s), the failure brought the form back in 0.5 s with the name,
the address, the TTL and the cloud as typed."""
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")


def _save():
    return APP.split("window.dnsSave = async (e, k) => {", 1)[1].split("window.dnsDel = async k => {", 1)[0]


def test_the_box_closes_before_the_provider_answers_and_says_it_is_saving():
    s = _save()
    assert s.index("closeModal();") < s.index("await api(")      # (closed first, then the call)
    assert 'toast(r ? "Saving the record…" : "Making the record…");' in s
    assert 'const btn = f.querySelector(".qp-pri"); btn.disabled = true;' not in s      # (no dimmed Save to wait for: the box is gone)


def test_the_answer_is_a_message_and_a_refusal_brings_the_form_back_with_what_was_typed():
    s = _save()
    assert 'toast(r ? "✓ Record changed" : "✓ Record made", "ok"); refresh();' in s
    assert 'catch (err) { toast("✕ " + err.message, "bad"); if (state.page === "tunnels" && sub("tunnels") === "dns" && !$("#modal .modal")) { dnsForm(k); setTimeout(() => dnsRestore(vals, err.message), 140); } }' in s      # (only while the CDN page is still open and nothing else is on the screen)
    assert "const vals = {account: f.elements.account.value, domain: f.elements.domain.value, rtype: rt, name: f.elements.name.value, ips: f.elements.ips.value, ttl: f.elements.ttl.value, cloud: f.elements.cloud.checked," in s
    restore = APP.split("window.dnsRestore = (v, msg) => {", 1)[1].split("window.dnsDel = async k => {", 1)[0]
    for field in ('set("ttl", v.ttl)', 'set("name", v.name)', 'set("ips", v.ips)', "f.elements.cloud.checked = v.cloud", 'set("port", v.port)', 'set("upstream", v.upstream)', "formErr(f, msg)"):
        assert field in restore
    assert '!f.elements.account.disabled) { set("account", v.account); dnsFormAcc(f); set("domain", v.domain);' in restore      # (a new record: the account and the domain are put back before the rest; an edited one has them fixed)