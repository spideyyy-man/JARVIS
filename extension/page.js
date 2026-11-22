// Functions injected into the ACTIVE tab when JARVIS asks (chrome.scripting.executeScript serialises each function, so
// each one is self-contained). They never run on their own and never submit a form.

// Fields JARVIS must never touch: passwords, OTPs, card/bank/ID numbers (also refused on the JARVIS side).
const JARVIS_SENSITIVE_SOURCE = String.raw`pass\s*(?:word|code)|\botp\b|one[\s-]?time|\bcvv\b|\bcvc\b|card\s*(?:number|no)|credit\s*card|debit\s*card|expir|aadha+r|\bpan\b|bank\s*(?:account|a\/c)|account\s*(?:number|no)|\bifsc\b|\bupi\b|\bpin\b(?!\s*code)|\bssn\b|social\s*security|passport|security\s*(?:code|question)|\bmpin\b|login\s*id|user\s*name|username`;

// The page's own content: <main>/<article> when there is one, without menus, headers, footers and side bars (a summary of
// "Level Slider, My Profile, Log Out" was all the owner got from a learning site - 2026-11-22). Runs in every frame.
function jarvisReadPage() {
  if (!document.body) return { title: document.title, url: location.href, text: "" };
  // innerText: only what is on screen. Hidden parts (closed "Click for Explanation" pop-ups that mark the correct phrase,
  // tooltips) are not read - JARVIS works from what the owner can see, never from a page's hidden answer key.
  const main = [...document.querySelectorAll('main, [role="main"], article')].find((m) => (m.innerText || "").trim().length > 200);
  const root = main || document.body;
  let text = root.innerText || "";
  for (const e of root.querySelectorAll('nav, header, footer, aside, [role="navigation"], [role="banner"], [role="contentinfo"], ' +
                                        '[role="menu"], [role="menubar"], [role="toolbar"], button')) {
    const t = (e.innerText || "").trim();
    if (t) text = text.replace(t, "");
  }
  text = text.split("\n").map((l) => l.replace(/\s+/g, " ").trim()).filter(Boolean).join("\n");
  return { title: document.title, url: location.href, text: text.slice(0, 20000) };
}

function jarvisReadForm(sensitiveSource) {
  const SENS = new RegExp(sensitiveSource, "i");
  const clean = (t) => (t || "").replace(/\s+/g, " ").replace(/\s*\*\s*$/, "").trim();
  const textOf = (el) => clean(el ? el.innerText || el.textContent || "" : "");
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return (r.width > 0 || r.height > 0) && s.visibility !== "hidden" && s.display !== "none";
  };
  window.__jarvisN = window.__jarvisN || 0;
  const tag = (el) => {
    if (!el.dataset.jarvisId) el.dataset.jarvisId = "j" + ++window.__jarvisN;
    return el.dataset.jarvisId;
  };
  const container = (el) => el.closest('[role="listitem"], fieldset, .form-group, .field, .form-field, .question, li, tr, p, div');
  const labelOf = (el) => {
    if (el.labels && el.labels.length) {
      const t = clean([...el.labels].map(textOf).join(" "));
      if (t) return t;
    }
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = clean(lb.split(/\s+/).map((i) => textOf(document.getElementById(i))).join(" "));
      if (t) return t;
    }
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    const item = el.closest('[role="listitem"], fieldset');
    if (item) {
      const h = item.querySelector('[role="heading"], legend');
      if (h && textOf(h)) return textOf(h).slice(0, 120);
    }
    if (el.placeholder) return clean(el.placeholder);
    // the nearest text before the field: its own siblings first, then its wrappers' (a fill-in box whose sentence start
    // sits one level up was left without a question - 2026-11-22), up to 4 levels; a long passage gives its last words
    // Text before the field counts first, at every level; the wrapper's own text only after that, and never a bare
    // number - on the owner's Pearson task the box's score ("20") sat beside it and became the "question".
    const worth = (t) => t && t.length >= 3 && !/^[\d\s.,:/%()+-]+$/.test(t);
    for (let node = el, up = 0; node && node !== document.body && up < 4; node = node.parentElement, up++) {
      for (let prev = node.previousElementSibling; prev; prev = prev.previousElementSibling) {
        const t = textOf(prev);
        if (worth(t)) return t.length > 200 ? t.slice(-200) : t;
      }
    }
    for (let node = el.parentElement, up = 0; node && node !== document.body && up < 4; node = node.parentElement, up++) {
      const t = textOf(node);
      if (worth(t) && t.length < 300) return t;
    }
    return el.name || el.id || "";
  };
  const required = (el) => !!(el.required || el.getAttribute("aria-required") === "true" || /\*\s*$/.test(labelOf(el)));
  const fields = [];
  const seenGroups = new Set();

  for (const el of document.querySelectorAll("input, textarea, select")) {
    const type = (el.type || el.tagName).toLowerCase();
    // styled radios/checkboxes hide the real input (0x0) and show its label instead - those count when the label shows
    const shown = visible(el) || ((type === "radio" || type === "checkbox") &&
      ((el.labels && [...el.labels].some(visible)) || (el.parentElement && visible(el.parentElement))));
    if (["hidden", "submit", "button", "reset", "image", "file"].includes(type) || el.disabled || el.readOnly || !shown) continue;
    const label = labelOf(el);
    const sensitive = type === "password" || /^cc-|password|one-time-code/.test(el.autocomplete || "") || SENS.test(`${label} ${el.name} ${el.id}`);
    if (type === "radio" || type === "checkbox") {
      const name = el.name || tag(el);
      const key = type + ":" + name;
      if (seenGroups.has(key)) continue;
      seenGroups.add(key);
      const group = el.name ? [...document.querySelectorAll(`input[type="${type}"][name="${CSS.escape(el.name)}"]`)] : [el];
      const groupBox = el.closest('[role="listitem"], fieldset') || container(el);
      const head = groupBox ? groupBox.querySelector('legend, [role="heading"]') : null;
      const options = group.map((o) => labelOf(o) || o.value);
      // no legend/heading: the question is the text around the options (quiz pages), without the options themselves
      let question = head ? textOf(head) : "";
      if (!question && group.length > 1) {
        let box = group[0].parentElement;
        while (box && !group.every((o) => box.contains(o))) box = box.parentElement;
        for (let up = 0; box && up < 3; up++, box = box.parentElement) {
          let t = textOf(box);
          for (const o of options) t = t.split(o).join(" ");
          t = clean(t);
          if (t.length >= 3) { question = t.slice(0, 300); break; }
        }
      }
      fields.push({ id: tag(el), kind: type, label: question || (group.length > 1 ? (el.name || label) : label), name: el.name || "",
                    options: group.length > 1 || type === "radio" ? options : [], required: group.some(required),
                    value: group.filter((o) => o.checked).map((o) => labelOf(o) || o.value).join(", "), sensitive });
      continue;
    }
    const kind = el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : type;
    fields.push({ id: tag(el), kind, label, name: el.name || "", autocomplete: el.autocomplete || "", required: required(el),
                  options: kind === "select" ? [...el.options].filter((o) => o.value !== "" || o.text.trim()).map((o) => clean(o.text)).filter(Boolean) : [],
                  value: sensitive ? "" : el.value || "", sensitive });
  }
  // Google Forms and similar: ARIA radio groups, checkbox lists, dropdowns (listbox), rich text boxes
  for (const g of document.querySelectorAll('[role="radiogroup"]')) {
    if (!visible(g)) continue;
    const opts = [...g.querySelectorAll('[role="radio"]')];
    if (!opts.length) continue;
    const item = g.closest('[role="listitem"]') || container(g);
    const head = item ? item.querySelector('[role="heading"]') : null;
    const label = head ? textOf(head) : g.getAttribute("aria-label") || "";
    fields.push({ id: tag(g), kind: "aria-radio", label, name: "", options: opts.map((o) => clean(o.getAttribute("aria-label") || o.dataset.value || textOf(o))),
                  required: /\*/.test(item ? textOf(item).slice(0, 200) : "") || g.getAttribute("aria-required") === "true",
                  value: opts.filter((o) => o.getAttribute("aria-checked") === "true").map((o) => o.getAttribute("aria-label") || "").join(", "),
                  sensitive: SENS.test(label) });
  }
  for (const item of document.querySelectorAll('[role="listitem"]')) {
    const boxes = [...item.querySelectorAll('[role="checkbox"]')];
    if (!boxes.length || !visible(item) || item.querySelector('[role="radiogroup"]')) continue;
    const head = item.querySelector('[role="heading"]');
    fields.push({ id: tag(item), kind: "aria-checkbox", label: head ? textOf(head) : "", name: "",
                  options: boxes.map((o) => clean(o.getAttribute("aria-label") || o.dataset.answerValue || textOf(o))), required: false,
                  value: boxes.filter((o) => o.getAttribute("aria-checked") === "true").map((o) => o.getAttribute("aria-label") || "").join(", "),
                  sensitive: false });
  }
  for (const lb of document.querySelectorAll('[role="listbox"]')) {
    if (!visible(lb) || lb.closest("select")) continue;
    const item = lb.closest('[role="listitem"]') || container(lb);
    const head = item ? item.querySelector('[role="heading"]') : null;
    const opts = [...lb.querySelectorAll('[role="option"]')].map((o) => clean(o.dataset.value || o.getAttribute("aria-label") || textOf(o))).filter((t) => t && !/^choose$/i.test(t));
    fields.push({ id: tag(lb), kind: "aria-listbox", label: head ? textOf(head) : lb.getAttribute("aria-label") || "", name: "", options: opts,
                  required: false, value: "", sensitive: false });
  }
  for (const ed of document.querySelectorAll('[contenteditable="true"][role="textbox"]')) {
    if (!visible(ed)) continue;
    fields.push({ id: tag(ed), kind: "contenteditable", label: labelOf(ed), name: "", options: [], required: required(ed), value: textOf(ed), sensitive: SENS.test(labelOf(ed)) });
  }
  // Custom quiz widgets: a question followed by 2-8 clickable answer boxes (div/li/button/label) and no form inputs - the
  // owner's learning site "searched for text boxes when there is only option for selecting" (2026-11-22).
  const navZone = (el) => el.closest('nav, header, footer, aside, [role="navigation"], [role="menu"], [role="menubar"], [role="tablist"], [role="toolbar"]');
  const clickable = (el) => {
    const role = el.getAttribute("role") || "";
    return el.tagName === "BUTTON" || ["button", "option", "radio", "menuitemradio", "checkbox"].includes(role) || el.hasAttribute("onclick") ||
      (el.hasAttribute("tabindex") && el.getAttribute("tabindex") !== "-1") || getComputedStyle(el).cursor === "pointer";
  };
  const optText = (el) => clean(el.innerText || el.textContent || "");
  const chosen = [];
  for (const box of document.querySelectorAll('ul, ol, div, fieldset, [role="list"], [role="listbox"], [role="group"]')) {
    if (chosen.length >= 60) break;
    if (box.dataset.jarvisChoice || !visible(box) || navZone(box) || box.querySelector('input:not([type="hidden"]), select, textarea, [role="radiogroup"], [role="radio"][data-jarvis-id]')) continue;
    if (chosen.some((c) => c.contains(box) || box.contains(c))) continue;
    const kids = [...box.children].filter(visible);
    if (kids.length < 2 || kids.length > 8) continue;
    const texts = kids.map(optText);
    if (texts.some((t) => !t || t.length > 200) || new Set(kids.map((k) => k.tagName)).size > 1) continue;
    if (kids.some((k) => k.matches('a[href]:not([href^="#"]):not([href^="javascript"])') || k.querySelector('a[href]:not([href^="#"]):not([href^="javascript"])'))) continue;
    if (!kids.every((k) => clickable(k) || [...k.querySelectorAll("*")].slice(0, 20).some(clickable))) continue;
    // the question: the nearest text before the options (its own short block when the passage is long)
    let question = "";
    for (let node = box, up = 0; node && up < 3 && !question; node = node.parentElement, up++) {
      for (let prev = node.previousElementSibling; prev && !question; prev = prev.previousElementSibling) {
        const t = optText(prev);
        if (t.length >= 3 && !texts.includes(t)) question = t.length > 300 ? t.slice(-300) : t;
      }
    }
    if (!question) continue;
    chosen.push(box);
    box.dataset.jarvisChoice = "1";
    const picked = kids.filter((k) => k.getAttribute("aria-checked") === "true" || k.getAttribute("aria-selected") === "true" ||
      k.getAttribute("aria-pressed") === "true" || /\b(selected|active|checked|chosen)\b/i.test(k.className || ""));
    fields.push({ id: tag(box), kind: "choice", label: question, name: "", options: texts, required: false,
                  value: picked.map(optText).join(", "), sensitive: SENS.test(question) });
  }
  // in the order they appear on the page (question 1 first), whatever kind of field they are
  const at = (f) => document.querySelector(`[data-jarvis-id="${CSS.escape(f.id)}"]`);
  fields.sort((a, b) => {
    const x = at(a), y = at(b);
    return x && y && x !== y ? (x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1) : 0;
  });
  return { title: document.title, url: location.href, fields };
}

async function jarvisFill(values, sensitiveSource) {
  const SENS = new RegExp(sensitiveSource, "i");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim().toLowerCase();
  const pick = (value, options) => {
    const v = clean(value);
    return options.find((o) => o.text === v) || options.find((o) => o.text && (o.text.startsWith(v) || v.startsWith(o.text))) || options.find((o) => v && o.text.includes(v));
  };
  const setValue = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    el.focus();
    setter.call(el, value); // the native setter, so React/Angular forms notice the change
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  };
  const mark = (el) => {
    el.style.outline = "2px solid #22d3ee";
    setTimeout(() => (el.style.outline = ""), 2500);
  };
  const filled = [], skipped = [];
  for (const { id, value } of values) {
    const el = document.querySelector(`[data-jarvis-id="${CSS.escape(id)}"]`);
    if (!el) { skipped.push({ id, reason: "the field is no longer on the page" }); continue; }
    const label = el.getAttribute("aria-label") || el.name || id;
    try {
      if ((el.type || "").toLowerCase() === "password" || /^cc-|password|one-time-code/.test(el.autocomplete || "") || SENS.test(`${el.name || ""} ${el.id || ""} ${el.getAttribute("aria-label") || ""}`)) {
        skipped.push({ id, label, reason: "JARVIS never fills passwords, OTPs, card or ID numbers" });
        continue;
      }
      const type = (el.type || "").toLowerCase();
      if (el.tagName === "SELECT") {
        const o = pick(value, [...el.options].map((x) => ({ text: clean(x.text), el: x })));
        if (!o) throw new Error(`no option "${value}"`);
        setValue(el, o.el.value);
      } else if (type === "radio" || type === "checkbox") {
        const group = el.name ? [...document.querySelectorAll(`input[type="${type}"][name="${CSS.escape(el.name)}"]`)] : [el];
        const wanted = type === "checkbox" ? String(value).split(/\s*,\s*/) : [String(value)];
        for (const w of wanted) {
          const optLabel = (o) => clean((o.labels && o.labels[0] ? o.labels[0].innerText : "") || o.value);
          const o = group.length === 1 && /^(yes|true|agree|ok|1|on|checked)$/i.test(w) ? { el: group[0] } : pick(w, group.map((x) => ({ text: optLabel(x), el: x })));
          if (!o) throw new Error(`no option "${w}"`);
          if (!o.el.checked) o.el.click();
        }
      } else if (el.getAttribute("role") === "radiogroup") {
        const o = pick(value, [...el.querySelectorAll('[role="radio"]')].map((x) => ({ text: clean(x.getAttribute("aria-label") || x.dataset.value || x.innerText), el: x })));
        if (!o) throw new Error(`no option "${value}"`);
        o.el.click();
      } else if (el.getAttribute("role") === "listitem") {
        for (const w of String(value).split(/\s*,\s*/)) {
          const o = pick(w, [...el.querySelectorAll('[role="checkbox"]')].map((x) => ({ text: clean(x.getAttribute("aria-label") || x.dataset.answerValue || x.innerText), el: x })));
          if (!o) throw new Error(`no option "${w}"`);
          if (o.el.getAttribute("aria-checked") !== "true") o.el.click();
        }
      } else if (el.getAttribute("role") === "listbox") {
        el.click(); // open the dropdown, then pick the option from the list it shows
        await sleep(400);
        const opts = [...document.querySelectorAll('[role="option"]')].filter((x) => x.getBoundingClientRect().height > 0);
        const o = pick(value, opts.map((x) => ({ text: clean(x.dataset.value || x.getAttribute("aria-label") || x.innerText), el: x })));
        if (!o) throw new Error(`no option "${value}"`);
        o.el.click();
        await sleep(300);
      } else if (el.dataset.jarvisChoice) {  // a custom answer box: click the option whose text matches
        const o = pick(value, [...el.children].map((x) => ({ text: clean(x.innerText || x.textContent), el: x })));
        if (!o) throw new Error(`no option "${value}"`);
        const target = o.el.querySelector('input, button, [role="radio"], [role="option"], [role="button"], [tabindex]') || o.el;
        target.scrollIntoView({ block: "center" });
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup"]) {
          const Ev = type.startsWith("pointer") && typeof PointerEvent === "function" ? PointerEvent : MouseEvent;
          target.dispatchEvent(new Ev(type, { bubbles: true, cancelable: true, view: window }));
        }
        target.click();
        await sleep(150);
        mark(o.el);
        filled.push(id);
        continue;
      } else if (el.isContentEditable) {
        el.focus();
        document.execCommand("selectAll", false);
        document.execCommand("insertText", false, String(value));
      } else {
        setValue(el, String(value));
      }
      mark(el);
      filled.push(id);
    } catch (e) {
      skipped.push({ id, label, reason: String(e.message || e) });
    }
  }
  return { filled, skipped };
}

function jarvisClick(text) {
  const want = (text || "").replace(/\s+/g, " ").trim().toLowerCase();
  const cands = [...document.querySelectorAll('button, a, [role="button"], input[type="submit"], input[type="button"], [role="link"], [role="tab"], [role="menuitem"], summary, label')]
    .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const name = (el) => (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").replace(/\s+/g, " ").trim();
  const el = cands.find((c) => name(c).toLowerCase() === want) || cands.find((c) => name(c).toLowerCase().includes(want));
  if (!el) return null;  // not in this frame (the background script tries every frame)
  el.scrollIntoView({ block: "center" });
  el.click();
  return { clicked: name(el).slice(0, 80) };
}

function jarvisScroll(direction) {
  const h = window.innerHeight * 0.8;
  if (direction === "top") window.scrollTo({ top: 0, behavior: "smooth" });
  else if (direction === "bottom") window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  else window.scrollBy({ top: direction === "up" ? -h : h, behavior: "smooth" });
  return { scrolled: direction };
}

// Read-only diagnosis for the owner's JARVIS (GET /api/connector/inspect): how this frame is built - which kinds of
// fields it has, embedded frames, web components (shadow roots) - and the markup around a piece of text. Nothing is changed.
function jarvisInspect(probe) {
  const short = (s, n) => (s || "").replace(/\s+/g, " ").trim().slice(0, n);
  const all = [...document.querySelectorAll("*")];
  const shadowHosts = all.filter((e) => e.shadowRoot);
  const count = (sel) => document.querySelectorAll(sel).length;
  const out = {
    url: location.href.slice(0, 200), title: document.title,
    counts: { input: count("input"), textarea: count("textarea"), select: count("select"), contenteditable: count("[contenteditable]"),
              textbox_role: count('[role="textbox"]'), iframe: count("iframe"), shadow_hosts: shadowHosts.length, elements: all.length },
    iframes: [...document.querySelectorAll("iframe")].slice(0, 10).map((f) => ({ src: short(f.src, 150), w: f.clientWidth, h: f.clientHeight })),
    shadow_hosts: shadowHosts.slice(0, 15).map((e) => ({ tag: e.tagName.toLowerCase(), mode: "open",
      inner: { textarea: e.shadowRoot.querySelectorAll("textarea").length, input: e.shadowRoot.querySelectorAll("input").length,
               editable: e.shadowRoot.querySelectorAll("[contenteditable]").length } })),
    editables: [...document.querySelectorAll('textarea, [contenteditable]:not([contenteditable="false"]), [role="textbox"], input:not([type="hidden"])')]
      .slice(0, 20).map((e) => ({ tag: e.tagName.toLowerCase(), type: e.type || "", role: e.getAttribute("role") || "", ce: e.getAttribute("contenteditable"),
        cls: short(e.className && e.className.baseVal === undefined ? e.className : "", 80), disabled: !!e.disabled, readonly: !!e.readOnly,
        size: `${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)}`,
        jarvis: e.dataset.jarvisId || "", parentText: short(e.parentElement && e.parentElement.innerText, 120) })),
    around: [],
  };
  if (probe) {
    const want = probe.toLowerCase();
    const hits = all.filter((e) => e.children.length < 8 && (e.innerText || "").toLowerCase().includes(want)).slice(-3);
    for (const h of hits) {
      let box = h;
      for (let i = 0; i < 3 && box.parentElement && box.parentElement !== document.body; i++) box = box.parentElement;
      const clone = box.cloneNode(true);
      clone.querySelectorAll("script, style, svg").forEach((x) => x.remove());
      out.around.push(short(clone.outerHTML.replace(/>\s+</g, "><"), 2500));
    }
  }
  return out;
}
