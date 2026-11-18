/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Custom, accessible form controls (no native selects, checkboxes or number inputs). */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls = {};

    function fieldShell(id, label, control, hint, required) {
        const error = h("div", { class: "field-error hidden", id: id + "-err", role: "alert" });
        const el = h("div", { class: "field" },
            label ? h("label", { for: id, class: "label", id: id + "-lbl" }, label,
                required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : null) : null,
            control,
            hint ? h("div", { class: "hint", id: id + "-hint" }, hint) : null,
            error);
        return { el, error };
    }

    /* ---------------------------------------------------------------- text / password */

    C.text = function ({ label, value, placeholder, hint, required, mono, disabled, password, autocomplete }) {
        const id = SH.id("f");
        const input = h("input", {
            type: password ? "password" : "text", id, class: "input" + (mono ? " mono" : ""),
            placeholder, disabled, autocomplete: autocomplete || (password ? "new-password" : "off"),
            spellcheck: "false", "aria-describedby": hint ? id + "-hint" : null,
        });
        input.value = value === undefined || value === null ? "" : String(value);
        let control = input;
        if (password) {
            const eye = h("button", { type: "button", class: "input-addon", "aria-label": "Show password", "aria-pressed": "false" }, "Show");
            eye.addEventListener("click", () => {
                const show = input.type === "password";
                input.type = show ? "text" : "password";
                eye.textContent = show ? "Hide" : "Show";
                eye.setAttribute("aria-pressed", String(show));
            });
            control = h("div", { class: "input-group" }, input, eye);
        }
        const { el, error } = fieldShell(id, label, control, hint, required);
        return {
            el, input,
            get: () => (password ? input.value : input.value.trim()),
            set: v => { input.value = v; },
            setDisabled: v => { input.disabled = !!v; },
            setError(msg) {
                error.textContent = msg || "";
                error.classList.toggle("hidden", !msg);
                input.classList.toggle("invalid", !!msg);
                input.setAttribute("aria-invalid", msg ? "true" : "false");
                if (msg) input.focus();
            },
        };
    };

    /* ---------------------------------------------------------------- switch */

    C.toggle = function ({ label, checked, hint, onChange, disabled }) {
        const id = SH.id("sw");
        const btn = h("button", {
            type: "button", role: "switch", class: "sw", id,
            "aria-checked": String(!!checked), "aria-labelledby": id + "-lbl",
            "aria-describedby": hint ? id + "-hint" : null,
        }, h("span", { class: "sw-knob" }));
        const text = h("span", { class: "toggle-label", id: id + "-lbl" }, label);
        const row = h("div", { class: "toggle-row" }, btn,
            h("div", {}, text, hint ? h("div", { class: "hint", id: id + "-hint" }, hint) : null));
        const get = () => btn.getAttribute("aria-checked") === "true";
        const set = v => btn.setAttribute("aria-checked", String(!!v));
        btn.addEventListener("click", () => { set(!get()); if (onChange) onChange(get()); });
        text.addEventListener("click", () => { if (!btn.disabled) btn.click(); });
        const api = {
            el: row, get, set,
            setDisabled(v) { btn.disabled = !!v; row.classList.toggle("disabled", !!v); },
        };
        if (disabled) api.setDisabled(true);
        return api;
    };

    /* ---------------------------------------------------------------- checkbox */

    C.checkbox = function ({ label, checked, onChange, description, disabled }) {
        const id = SH.id("cb");
        const box = h("button", { type: "button", role: "checkbox", class: "cb", id, "aria-checked": String(!!checked), "aria-labelledby": id + "-lbl" },
            h("span", { class: "cb-mark", "aria-hidden": "true" }, "✓"));
        const row = h("div", { class: "cb-row" }, box,
            h("div", {}, h("span", { class: "toggle-label", id: id + "-lbl" }, label),
                description ? h("div", { class: "hint" }, description) : null));
        const get = () => box.getAttribute("aria-checked") === "true";
        box.addEventListener("click", () => { box.setAttribute("aria-checked", String(!get())); if (onChange) onChange(get()); });
        row.querySelector(".toggle-label").addEventListener("click", () => { if (!box.disabled) box.click(); });
        const api = {
            el: row, get,
            set: v => box.setAttribute("aria-checked", String(!!v)),
            setDisabled(v) { box.disabled = !!v; row.classList.toggle("disabled", !!v); },
        };
        if (disabled) api.setDisabled(true);
        return api;
    };

    /* ---------------------------------------------------------------- listbox popup (shared) */

    function popupList({ id, label, multi, getOptions, isSelected, onPick, emptyText }) {
        const list = h("ul", { class: "ms-list hidden", id: id + "-list", role: "listbox", "aria-label": label, "aria-multiselectable": multi ? "true" : null });
        let active = 0;
        let filtered = [];
        function render(query, input) {
            const q = (query || "").toLowerCase();
            filtered = getOptions().filter(o => (o.label + " " + (o.meta || "")).toLowerCase().includes(q));
            if (active >= filtered.length) active = Math.max(0, filtered.length - 1);
            if (!filtered.length) {
                list.replaceChildren(h("li", { class: "ms-empty" }, getOptions().length ? "No matches" : (emptyText || "Nothing to choose")));
                if (input) input.removeAttribute("aria-activedescendant");
                return;
            }
            list.replaceChildren(...filtered.map((o, i) => {
                const li = h("li", {
                    id: `${id}-opt-${i}`, role: "option", class: "ms-opt" + (i === active ? " active" : ""),
                    "aria-selected": String(isSelected(o.value)),
                }, h("span", { class: "ms-check", "aria-hidden": "true" }, isSelected(o.value) ? "✓" : ""),
                o.icon ? h("span", { class: "opt-icon", "aria-hidden": "true" }, o.icon()) : null,
                h("span", { class: "ms-text" }, o.label, o.meta ? h("span", { class: "ms-meta" }, o.meta) : null));
                li.addEventListener("mousedown", ev => { ev.preventDefault(); active = i; onPick(o.value); });
                return li;
            }));
            if (input) input.setAttribute("aria-activedescendant", `${id}-opt-${active}`);
            const cur = list.children[active];
            if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
        }
        return {
            list, render,
            move(delta) { active = Math.max(0, Math.min(active + delta, filtered.length - 1)); },
            current: () => filtered[active],
            reset() { active = 0; },
        };
    }

    function normOptions(options) {
        return (options || []).map(o => (typeof o === "string" ? { value: o, label: o } : o));
    }

    /* ---------------------------------------------------------------- single select (custom dropdown) */

    C.select = function ({ label, options, value, onChange, hint, searchable, disabled, onDescribe }) {
        const id = SH.id("sel");
        let opts = normOptions(options);
        let current = value;
        let open = false;
        const btnIcon = h("span", { class: "opt-icon hidden", "aria-hidden": "true" });
        const btnText = h("span", { class: "sel-value" });
        const button = h("button", {
            type: "button", class: "sel-button", id, "aria-haspopup": "listbox", "aria-expanded": "false",
            "aria-controls": id + "-list", "aria-labelledby": `${id}-lbl ${id}`,
        }, h("span", { class: "sel-left" }, btnIcon, btnText), h("span", { class: "sel-caret", "aria-hidden": "true" }, "▾"));
        const search = searchable ? h("input", { type: "text", class: "input sel-search", placeholder: "Search…", "aria-label": `Search ${label}`, autocomplete: "off" }) : null;
        const popup = popupList({
            id, label, multi: false, getOptions: () => opts, isSelected: v => v === current,
            onPick: v => { setValue(v, true); close(); button.focus(); },
        });
        const panel = h("div", { class: "sel-panel hidden" }, search, popup.list);
        popup.list.classList.remove("hidden");
        const root = h("div", { class: "sel" }, button, panel);
        const { el } = fieldShell(id, label, root, hint);

        function describe() {
            const o = opts.find(x => x.value === current);
            btnText.textContent = o ? o.label : "Select…";
            btnIcon.replaceChildren(o && o.icon ? o.icon() : "");
            btnIcon.classList.toggle("hidden", !(o && o.icon));
            if (onDescribe) onDescribe(o);
        }
        function setValue(v, fire) {
            current = v;
            describe();
            if (fire && onChange) onChange(v);
        }
        function openList() {
            open = true;
            panel.classList.remove("hidden");
            button.setAttribute("aria-expanded", "true");
            popup.reset();
            const idx = opts.findIndex(o => o.value === current);
            if (idx > 0) popup.move(idx);
            popup.render(search ? search.value : "", null);
            if (search) { search.value = ""; popup.render("", null); search.focus(); }
        }
        function close() {
            open = false;
            panel.classList.add("hidden");
            button.setAttribute("aria-expanded", "false");
        }
        button.addEventListener("click", () => (open ? close() : openList()));
        const keyHandler = ev => {
            if (ev.key === "ArrowDown") { ev.preventDefault(); if (!open) openList(); else { popup.move(1); popup.render(search ? search.value : ""); } }
            else if (ev.key === "ArrowUp") { ev.preventDefault(); popup.move(-1); popup.render(search ? search.value : ""); }
            else if (ev.key === "Enter" && open) { ev.preventDefault(); const o = popup.current(); if (o) { setValue(o.value, true); close(); button.focus(); } }
            else if (ev.key === "Escape" && open) { ev.preventDefault(); ev.stopPropagation(); close(); button.focus(); }
        };
        button.addEventListener("keydown", keyHandler);
        if (search) {
            search.addEventListener("keydown", keyHandler);
            search.addEventListener("input", () => { popup.reset(); popup.render(search.value); });
        }
        root.addEventListener("focusout", () => setTimeout(() => { if (!root.contains(document.activeElement)) close(); }, 0));
        describe();
        const api = {
            el, get: () => current, set: v => setValue(v, false),
            setOptions(o) { opts = normOptions(o); describe(); },
            setDisabled(v) { button.disabled = !!v; },
        };
        if (disabled) api.setDisabled(true);
        return api;
    };

    /* ---------------------------------------------------------------- multi select with chips + search */

    C.multiSelect = function ({ label, options, selected, placeholder, hint, emptyText, onChange }) {
        const id = SH.id("ms");
        const chosen = new Set(selected || []);
        let opts = normOptions(options);
        for (const v of chosen) if (!opts.some(o => o.value === v)) opts.push({ value: v, label: v });
        let open = false;
        const chips = h("div", { class: "ms-chips" });
        const input = h("input", {
            type: "text", id, class: "ms-input", placeholder, autocomplete: "off", spellcheck: "false",
            role: "combobox", "aria-expanded": "false", "aria-controls": id + "-list", "aria-autocomplete": "list",
        });
        const caret = h("button", { type: "button", class: "ms-caret", tabindex: "-1", "aria-label": "Show options" }, "▾");
        const popup = popupList({
            id, label, multi: true, emptyText, getOptions: () => opts, isSelected: v => chosen.has(v),
            onPick: v => { toggleValue(v); popup.render(input.value, input); },
        });
        const control = h("div", { class: "ms-control" }, chips, input, caret);
        const root = h("div", { class: "ms" }, control, popup.list);
        const { el } = fieldShell(id, label, root, hint);

        function renderChips() {
            chips.replaceChildren(...Array.from(chosen).sort().map(v => {
                const o = opts.find(x => x.value === v);
                return h("span", { class: "chip" }, o ? o.label : v,
                    h("button", {
                        type: "button", class: "chip-x", "aria-label": `Remove ${v}`,
                        onclick: ev => { ev.stopPropagation(); chosen.delete(v); renderChips(); popup.render(input.value, input); if (onChange) onChange(); input.focus(); },
                    }, "×"));
            }));
        }
        function toggleValue(v) {
            if (chosen.has(v)) chosen.delete(v); else chosen.add(v);
            renderChips();
            if (onChange) onChange();
        }
        function setOpen(v) {
            open = v;
            popup.list.classList.toggle("hidden", !v);
            root.classList.toggle("open", v);
            input.setAttribute("aria-expanded", String(v));
            if (v) popup.render(input.value, input); else input.removeAttribute("aria-activedescendant");
        }
        input.addEventListener("focus", () => setOpen(true));
        input.addEventListener("input", () => { popup.reset(); setOpen(true); });
        input.addEventListener("keydown", ev => {
            if (ev.key === "ArrowDown") { ev.preventDefault(); if (!open) setOpen(true); popup.move(1); popup.render(input.value, input); }
            else if (ev.key === "ArrowUp") { ev.preventDefault(); popup.move(-1); popup.render(input.value, input); }
            else if (ev.key === "Enter") { ev.preventDefault(); const o = popup.current(); if (open && o) { toggleValue(o.value); popup.render(input.value, input); } }
            else if (ev.key === "Escape" && open) { ev.preventDefault(); ev.stopPropagation(); setOpen(false); }
            else if (ev.key === "Backspace" && !input.value && chosen.size) {
                chosen.delete(Array.from(chosen).sort().pop()); renderChips(); popup.render("", input); if (onChange) onChange();
            }
        });
        input.addEventListener("blur", () => setTimeout(() => { if (!root.contains(document.activeElement)) setOpen(false); }, 0));
        control.addEventListener("mousedown", ev => {
            if (ev.target === input) return;
            ev.preventDefault();
            const wasOpen = open;
            input.focus();
            setOpen(ev.target === caret ? !wasOpen : true);
        });
        renderChips();
        return { el, get: () => Array.from(chosen) };
    };

    /* ---------------------------------------------------------------- tabs */

    C.tabs = function ({ items, active, onChange, small }) {
        const bar = h("div", { class: "tabs" + (small ? " tabs-sm" : ""), role: "tablist" });
        const buttons = items.map(it => {
            const b = h("button", {
                type: "button", role: "tab", class: "tab", id: "tab-" + it.id,
                "aria-selected": String(it.id === active), tabindex: it.id === active ? "0" : "-1",
            }, it.label, it.badge ? h("span", { class: "tab-badge" }, it.badge) : null);
            if (it.greyed) b.classList.add("tab-greyed");
            b.addEventListener("click", () => select(it.id, true));
            b.addEventListener("keydown", ev => {
                const i = items.findIndex(x => x.id === it.id);
                if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
                    ev.preventDefault();
                    const next = items[(i + (ev.key === "ArrowRight" ? 1 : items.length - 1)) % items.length];
                    select(next.id, true);
                    buttons[items.indexOf(next)].focus();
                }
            });
            bar.appendChild(b);
            return b;
        });
        function select(id, fire) {
            items.forEach((it, i) => {
                buttons[i].setAttribute("aria-selected", String(it.id === id));
                buttons[i].tabIndex = it.id === id ? 0 : -1;
            });
            if (fire && onChange) onChange(id);
        }
        return {
            el: bar, select,
            setGreyed(id, v) { const i = items.findIndex(x => x.id === id); if (i >= 0) buttons[i].classList.toggle("tab-greyed", !!v); },
        };
    };

    /* ---------------------------------------------------------------- modal + confirm */

    C.modal = function ({ title, body, actions, size, persistent }) {
        const titleId = SH.id("dlg");
        const previous = document.activeElement;
        const errorBox = h("div", { class: "modal-error hidden", role: "alert" });
        const dialog = h("div", { class: "modal" + (size ? " modal-" + size : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
            h("header", { class: "modal-head" },
                h("h2", { id: titleId }, title),
                persistent ? null : h("button", { type: "button", class: "icon-btn", "aria-label": "Close", onclick: () => close() }, "×")),
            h("div", { class: "modal-body" }, body),
            errorBox,
            h("footer", { class: "modal-foot" }, actions));
        const overlay = h("div", { class: "overlay" }, dialog);
        overlay.addEventListener("mousedown", ev => { if (ev.target === overlay && !persistent) close(); });
        dialog.setAttribute("tabindex", "-1");
        dialog.addEventListener("keydown", ev => {
            if (ev.key === "Escape") { ev.preventDefault(); if (!persistent) close(); }
            if (ev.key === "Tab") {
                const items = Array.from(dialog.querySelectorAll("button:not([disabled]), input:not([disabled])")).filter(x => x.offsetParent !== null);
                if (!items.length) return;
                const first = items[0], last = items[items.length - 1];
                if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
                else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
            }
        });
        document.getElementById("modal-root").appendChild(overlay);
        const first = dialog.querySelector(".modal-body input:not([disabled]), .modal-body button:not([disabled])");
        (first || dialog.querySelector(".modal-foot button") || dialog).focus();
        function close() {
            overlay.remove();
            if (previous && previous.focus) previous.focus();
        }
        return {
            close, dialog,
            setError(msg) { errorBox.textContent = msg || ""; errorBox.classList.toggle("hidden", !msg); if (msg) errorBox.scrollIntoView({ block: "nearest" }); },
            setBusy(v) {
                dialog.querySelectorAll(".modal-foot button").forEach(b => { b.disabled = v; });
                dialog.classList.toggle("is-busy", v);
            },
        };
    };

    C.confirm = function ({ title, message, confirmLabel, danger }) {
        return new Promise(resolve => {
            const yes = h("button", { type: "button", class: "btn " + (danger ? "btn-danger" : "btn-primary") }, confirmLabel || "Confirm");
            const no = h("button", { type: "button", class: "btn btn-secondary" }, "Cancel");
            const m = C.modal({ title, body: h("div", {}, [].concat(message).map(p => (typeof p === "string" ? h("p", {}, p) : p))), actions: [no, yes] });
            no.addEventListener("click", () => { m.close(); resolve(false); });
            yes.addEventListener("click", () => { m.close(); resolve(true); });
        });
    };

    /* Modal with a form; onSubmit returns a promise. Errors stay inside the dialog. */
    C.formModal = function ({ title, body, submitLabel, size, onSubmit, danger }) {
        const submit = h("button", { type: "button", class: "btn " + (danger ? "btn-danger" : "btn-primary") }, submitLabel || "Save");
        const cancel = h("button", { type: "button", class: "btn btn-secondary" }, "Cancel");
        const m = C.modal({ title, body, actions: [cancel, submit], size });
        cancel.addEventListener("click", () => m.close());
        submit.addEventListener("click", async () => {
            m.setError("");
            m.setBusy(true);
            try {
                const ok = await onSubmit(m);
                if (ok !== false) m.close();
            } catch (e) {
                m.setError(e.message);
            } finally {
                m.setBusy(false);
            }
        });
        m.dialog.addEventListener("keydown", ev => {
            if (ev.key === "Enter" && ev.target.tagName === "INPUT" && !ev.target.closest(".ms, .sel")) { ev.preventDefault(); submit.click(); }
        });
        return m;
    };

    /* ---------------------------------------------------------------- folder picker */

    C.folder = function ({ label, value, hint, required }) {
        const field = C.text({ label, value, hint, required, mono: true, placeholder: "/srv/shares/data" });
        const browse = h("button", { type: "button", class: "btn btn-secondary btn-inline" }, "Browse…");
        const parent = field.input.parentNode;
        const row = h("div", { class: "input-row" });
        parent.insertBefore(row, field.input.nextSibling);
        row.append(field.input, browse);
        browse.addEventListener("click", () => openBrowser(field.get() || "/", path => field.set(path), field.input));
        return field;
    };

    /* A new folder is selected, labelled "Folder created", then the label flies to the
     * path field it fills (only highlights when the system asks for reduced motion). */
    function celebrate(item, path, close, onPick, target) {
        const badge = h("span", { class: "created-badge" }, "Folder created");
        item.classList.add("is-new", "is-selected");
        item.setAttribute("aria-selected", "true");
        item.appendChild(badge);
        item.scrollIntoView({ block: "nearest" });
        const flash = () => {
            if (!target) return;
            target.classList.remove("flash");
            void target.offsetWidth; // restart the animation
            target.classList.add("flash");
            setTimeout(() => target.classList.remove("flash"), 1600);
        };
        setTimeout(() => {
            const from = badge.getBoundingClientRect();
            close();
            onPick(path);
            if (SH.reducedMotion() || !target || !from.width) { flash(); return; }
            const ghost = h("div", { class: "ghost-chip", "aria-hidden": "true" }, "Folder created");
            ghost.style.left = from.left + "px";
            ghost.style.top = from.top + "px";
            document.body.appendChild(ghost);
            const to = target.getBoundingClientRect();
            requestAnimationFrame(() => requestAnimationFrame(() => {
                ghost.classList.add("flying");
                ghost.style.transform = `translate(${to.left + 12 - from.left}px, ${to.top + to.height / 2 - from.top - 11}px) scale(0.85)`;
            }));
            let done = false;
            const finish = () => { if (done) return; done = true; ghost.remove(); flash(); };
            ghost.addEventListener("transitionend", finish);
            setTimeout(finish, 900);
        }, SH.reducedMotion() ? 900 : 750);
    }

    function openBrowser(start, onPick, target) {
        let current = start;
        const pathLabel = h("div", { class: "browser-path mono" });
        const list = h("ul", { class: "browser-list", role: "listbox", "aria-label": "Folders" });
        const select = h("button", { type: "button", class: "btn btn-primary" }, "Use this folder");
        const cancel = h("button", { type: "button", class: "btn btn-secondary" }, "Cancel");
        const newBtn = h("button", { type: "button", class: "btn btn-link" }, "New folder…");
        const filesBtn = SH.featureReady("files") ? C.button("Open in Files", () => SH.openInFiles(current), "link", { title: "Open this folder in Cockpit Files" }) : null;
        const m = C.modal({ title: "Choose a folder", body: h("div", {}, pathLabel, list, h("div", { class: "row-gap" }, newBtn, filesBtn)), actions: [cancel, select], size: "md" });
        async function load(path) {
            try {
                const data = await SH.call("browse", { path });
                current = data.path;
                pathLabel.textContent = current;
                const items = [];
                if (data.parent !== null) items.push({ name: "..", path: data.parent, up: true });
                data.dirs.forEach(d => items.push({ name: d, path: (current === "/" ? "" : current) + "/" + d }));
                list.replaceChildren(...items.map(it => {
                    const li = h("li", { role: "option", tabindex: "0", class: "browser-item" + (it.up ? " up" : ""), "data-name": it.up ? null : it.name },
                        h("span", { class: "folder-icon", "aria-hidden": "true" }, it.up ? "↰" : "📁"), it.name);
                    li.addEventListener("click", () => load(it.path));
                    li.addEventListener("keydown", ev => { if (ev.key === "Enter") load(it.path); });
                    return li;
                }));
                if (!data.dirs.length) list.appendChild(h("li", { class: "ms-empty" }, "No subfolders"));
                m.setError("");
            } catch (e) {
                m.setError(e.message);
            }
        }
        cancel.addEventListener("click", () => m.close());
        select.addEventListener("click", () => { onPick(current); m.close(); });
        newBtn.addEventListener("click", () => {
            const parent = current;
            newFolderDialog(parent, async path => {
                await load(parent);
                const name = path.slice(path.lastIndexOf("/") + 1);
                const item = Array.from(list.children).find(li => li.dataset.name === name);
                current = path;
                pathLabel.textContent = path;
                if (item) celebrate(item, path, () => m.close(), onPick, target);
                else { onPick(path); m.close(); }
            });
        });
        load(start);
    }

    function newFolderDialog(parent, done) {
        const st = SH.store.status;
        const name = C.text({ label: "Folder name", required: true });
        const owner = C.select({ label: "Owner", searchable: true, value: "root",
            options: ["root"].concat(st.users.filter(u => !u.domain).map(u => u.name)) });
        const group = C.select({ label: "Group", searchable: true, value: "root",
            options: ["root"].concat(st.groups.map(g => g.name)) });
        const mode = C.select({ label: "Permissions", value: "2770", options: [
            { value: "2770", label: "2770 - owner and group full, others none (recommended)" },
            { value: "2775", label: "2775 - group full, others read" },
            { value: "0700", label: "0700 - owner only" },
            { value: "0777", label: "0777 - everyone full (not recommended)" },
        ] });
        const body = h("div", { class: "form" }, h("p", { class: "muted mono" }, parent), name.el,
            h("div", { class: "grid-2" }, owner.el, group.el), mode.el);
        C.formModal({
            title: "New folder", body, submitLabel: "Create",
            onSubmit: async () => {
                if (!/^[^/\0]+$/.test(name.get()) || name.get() === "." || name.get() === "..") { name.setError("Enter a folder name without '/'."); return false; }
                const path = (parent === "/" ? "" : parent) + "/" + name.get();
                await SH.call("mkdir", { path, owner: owner.get(), group: group.get(), mode: mode.get() });
                done(path);
            },
        });
    }

    /* ---------------------------------------------------------------- small bits */

    C.badge = (text, kind) => h("span", { class: "badge" + (kind ? " badge-" + kind : "") }, text);

    C.dot = function (state) {
        const kind = state === "active" ? "ok" : state === "failed" ? "danger" : "off";
        return h("span", { class: "dot dot-" + kind, "aria-hidden": "true" });
    };

    C.alert = function (kind, title, text, action) {
        return h("div", { class: "alert alert-" + kind, role: kind === "danger" ? "alert" : "status" },
            h("div", { class: "alert-text" }, h("strong", {}, title), text ? h("div", {}, text) : null),
            action || null);
    };

    /* Why a feature (or one field) cannot be used, with Details and, when it helps, Install.
     * state: {state, reason, install}; onInstalled runs after a successful install. */
    C.featureNote = function (fid, state, { onInstalled, compact } = {}) {
        const st = state || SH.feature(fid);
        if (!st || st.state === "ready" || st.state === "off") return null;
        const label = { impossible: "Not possible here", needs_install: "Needs packages", needs_setup: "Needs setup" }[st.state] || "";
        const actions = [];
        if (st.state === "needs_install" && st.install && st.install.length)
            actions.push(C.button("Install", async () => { if (await SH.installPackages(st.install) && onInstalled) onInstalled(); }, "secondary", { class: "btn btn-sm btn-secondary" }));
        if (fid) actions.push(C.button("Details", () => SH.showComponent(fid), "link", { class: "btn btn-link btn-sm" }));
        return h("div", { class: "feature-note feature-note-" + st.state + (compact ? " compact" : ""), role: "note" },
            h("span", { class: "badge" + (st.state === "needs_install" ? " badge-info" : st.state === "needs_setup" ? " badge-warn" : "") }, label),
            h("span", { class: "feature-note-text" }, st.reason || ""),
            actions.length ? h("span", { class: "feature-note-actions" }, actions) : null);
    };

    /* Grey out a block of controls without losing their values. */
    C.greyed = function (content, note) {
        const box = h("div", { class: "greyed", "aria-disabled": "true" }, content);
        box.inert = true; // not focusable or clickable; the controls themselves are left untouched
        return h("div", { class: "greyed-wrap" }, note, box);
    };

    C.fieldset = (legend, ...children) => h("fieldset", { class: "group" }, h("legend", {}, legend), children);

    C.empty = (text, action) => h("div", { class: "empty" }, h("p", {}, text), action || null);

    C.button = (label, onclick, kind, attrs) =>
        h("button", Object.assign({ type: "button", class: "btn btn-" + (kind || "secondary"), onclick }, attrs || {}), label);
})();
