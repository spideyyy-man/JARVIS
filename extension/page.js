// Functions injected into the ACTIVE tab when JARVIS asks (chrome.scripting.executeScript serialises each function, so
// each one is self-contained). They never run on their own and never submit a form.

// Fields JARVIS must never touch: passwords, OTPs, card/bank/ID numbers (also refused on the JARVIS side).
const JARVIS_SENSITIVE_SOURCE = String.raw`pass\s*(?:word|code)|\botp\b|one[\s-]?time|\bcvv\b|\bcvc\b|card\s*(?:number|no)|credit\s*card|debit\s*card|expir|aadha+r|\bpan\b|bank\s*(?:account|a\/c)|account\s*(?:number|no)|\bifsc\b|\bupi\b|\bpin\b(?!\s*code)|\bssn\b|social\s*security|passport|security\s*(?:code|question)|\bmpin\b|login\s*id|user\s*name|username`;

function jarvisReadPage() {
  return { title: document.title, url: location.href, text: (document.body ? document.body.innerText : "").slice(0, 20000) };
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
    // the nearest text before the field in its container
    let node = el.previousElementSibling;
    while (node) {
      if (textOf(node)) return textOf(node).slice(0, 120);
      node = node.previousElementSibling;
    }
    const c = container(el);
    if (c && textOf(c) && textOf(c).length < 140) return textOf(c);
    return el.name || el.id || "";
  };
  const required = (el) => !!(el.required || el.getAttribute("aria-required") === "true" || /\*\s*$/.test(labelOf(el)));
  const fields = [];
  const seenGroups = new Set();

  for (const el of document.querySelectorAll("input, textarea, select")) {
    const type = (el.type || el.tagName).toLowerCase();
    if (["hidden", "submit", "button", "reset", "image", "file"].includes(type) || el.disabled || el.readOnly || !visible(el)) continue;
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
      fields.push({ id: tag(el), kind: type, label: head ? textOf(head) : group.length > 1 ? (el.name || label) : label, name: el.name || "",
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
  if (!el) throw new Error(`nothing called "${text}" to click on this page`);
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
