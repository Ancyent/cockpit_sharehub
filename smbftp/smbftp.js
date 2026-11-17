/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* cockpit-smbftp - Cockpit page for Samba shares with an optional FTP twin.
 * All privileged work happens in helper.py (spawned with superuser rights). */
"use strict";

const HELPER = "/usr/share/cockpit/smbftp/helper.py";

let status = null;
let busy = false;

/* ------------------------------------------------------------------ helper IPC */

function helper(command, payload) {
    return new Promise((resolve, reject) => {
        cockpit.spawn(["python3", HELPER, command], { superuser: "require", err: "message" })
            .input(payload ? JSON.stringify(payload) : "")
            .then(output => {
                let reply;
                try {
                    reply = JSON.parse(output);
                } catch (e) {
                    reject(new Error("Invalid helper output: " + output.slice(0, 300)));
                    return;
                }
                if (reply.ok) resolve(reply.data);
                else reject(new Error(reply.error));
            })
            .catch(ex => {
                const msg = ex.problem === "access-denied" || ex.problem === "not-authorized"
                    ? "Administrative access is required. Use \"Turn on administrative access\" in Cockpit."
                    : (ex.message || String(ex));
                reject(new Error(msg));
            });
    });
}

/* ------------------------------------------------------------------ DOM helpers */

function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") el.className = value;
        else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
        else el.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
        if (child === null || child === undefined || child === false) continue;
        el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return el;
}

let uid = 0;
const nextId = prefix => `${prefix}-${++uid}`;

function toast(message, kind) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.className = "toast toast-" + (kind || "success");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.add("hidden"), kind === "warning" ? 9000 : 4000);
}

/* ------------------------------------------------------------------ controls */

/* Text field with label, hint and inline error. */
function textField({ label, value, placeholder, hint, disabled, required, mono }) {
    const id = nextId("f");
    const input = h("input", {
        type: "text", id, class: "input" + (mono ? " mono" : ""), value: value || "",
        placeholder, disabled, "aria-describedby": hint ? id + "-hint" : null, autocomplete: "off", spellcheck: "false",
    });
    const error = h("div", { class: "field-error hidden", id: id + "-err", role: "alert" });
    const el = h("div", { class: "field" },
        h("label", { for: id, class: "label" }, label, required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : null),
        input,
        hint ? h("div", { class: "hint", id: id + "-hint" }, hint) : null,
        error);
    return {
        el,
        input,
        get: () => input.value.trim(),
        setError(msg) {
            error.textContent = msg || "";
            error.classList.toggle("hidden", !msg);
            input.classList.toggle("invalid", !!msg);
            input.setAttribute("aria-invalid", msg ? "true" : "false");
        },
    };
}

/* Accessible on/off switch (button role="switch"). */
function toggle({ label, checked, hint, onChange }) {
    const id = nextId("sw");
    const btn = h("button", { type: "button", role: "switch", class: "sw", id, "aria-checked": String(!!checked), "aria-labelledby": id + "-lbl" },
        h("span", { class: "sw-knob" }));
    const row = h("div", { class: "toggle-row" },
        btn,
        h("div", {},
            h("span", { class: "toggle-label", id: id + "-lbl" }, label),
            hint ? h("div", { class: "hint" }, hint) : null));
    const get = () => btn.getAttribute("aria-checked") === "true";
    const set = v => btn.setAttribute("aria-checked", String(!!v));
    btn.addEventListener("click", () => {
        set(!get());
        if (onChange) onChange(get());
    });
    row.querySelector(".toggle-label").addEventListener("click", () => btn.click());
    return {
        el: row,
        get,
        set,
        setDisabled(v) { btn.disabled = !!v; row.classList.toggle("disabled", !!v); },
    };
}

/* Searchable multi-select combobox with chips. */
function multiSelect({ label, options, selected, placeholder, hint, emptyText }) {
    const id = nextId("ms");
    const chosen = new Set(selected || []);
    const all = Array.from(new Set([...(options || []), ...chosen])).sort((a, b) => a.localeCompare(b));
    let open = false;
    let active = 0;
    let filtered = all;

    const chips = h("div", { class: "ms-chips" });
    const input = h("input", {
        type: "text", id, class: "ms-input", placeholder, autocomplete: "off", spellcheck: "false",
        role: "combobox", "aria-expanded": "false", "aria-controls": id + "-list", "aria-autocomplete": "list",
    });
    const caret = h("button", { type: "button", class: "ms-caret", tabindex: "-1", "aria-label": "Show options" }, "▾");
    const list = h("ul", { class: "ms-list hidden", id: id + "-list", role: "listbox", "aria-multiselectable": "true", "aria-label": label });
    const control = h("div", { class: "ms-control" }, chips, input, caret);
    const root = h("div", { class: "ms" }, control, list);
    const el = h("div", { class: "field" },
        h("label", { for: id, class: "label" }, label),
        root,
        hint ? h("div", { class: "hint" }, hint) : null);

    function renderChips() {
        chips.replaceChildren(...Array.from(chosen).sort().map(value =>
            h("span", { class: "chip" }, value,
                h("button", {
                    type: "button", class: "chip-x", "aria-label": `Remove ${value}`,
                    onclick: ev => { ev.stopPropagation(); chosen.delete(value); renderChips(); renderList(); input.focus(); },
                }, "×"))));
    }

    function renderList() {
        const q = input.value.trim().toLowerCase();
        filtered = all.filter(v => v.toLowerCase().includes(q));
        if (active >= filtered.length) active = Math.max(0, filtered.length - 1);
        if (!filtered.length) {
            list.replaceChildren(h("li", { class: "ms-empty" }, all.length ? "No matches" : (emptyText || "Nothing to choose")));
            input.removeAttribute("aria-activedescendant");
            return;
        }
        list.replaceChildren(...filtered.map((value, i) => {
            const optId = `${id}-opt-${i}`;
            const li = h("li", {
                id: optId, role: "option", class: "ms-opt" + (i === active ? " active" : ""),
                "aria-selected": String(chosen.has(value)),
            }, h("span", { class: "ms-check", "aria-hidden": "true" }, chosen.has(value) ? "✓" : ""), value);
            li.addEventListener("mousedown", ev => { ev.preventDefault(); toggleValue(value); active = i; renderList(); });
            return li;
        }));
        if (open) {
            input.setAttribute("aria-activedescendant", `${id}-opt-${active}`);
            const cur = list.children[active];
            if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
        }
    }

    function toggleValue(value) {
        if (chosen.has(value)) chosen.delete(value);
        else chosen.add(value);
        renderChips();
    }

    function setOpen(v) {
        open = v;
        list.classList.toggle("hidden", !v);
        root.classList.toggle("open", v);
        input.setAttribute("aria-expanded", String(v));
        if (v) renderList();
        else input.removeAttribute("aria-activedescendant");
    }

    input.addEventListener("focus", () => setOpen(true));
    input.addEventListener("input", () => { active = 0; setOpen(true); renderList(); });
    input.addEventListener("keydown", ev => {
        if (ev.key === "ArrowDown") { ev.preventDefault(); if (!open) setOpen(true); active = Math.min(active + 1, filtered.length - 1); renderList(); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); active = Math.max(active - 1, 0); renderList(); }
        else if (ev.key === "Enter") { ev.preventDefault(); if (open && filtered[active]) { toggleValue(filtered[active]); renderList(); } }
        else if (ev.key === "Escape" && open) { ev.preventDefault(); ev.stopPropagation(); setOpen(false); }
        else if (ev.key === "Backspace" && !input.value && chosen.size) {
            const last = Array.from(chosen).sort().pop();
            chosen.delete(last); renderChips(); renderList();
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
}

/* Modal dialog with focus handling. */
function openModal({ title, body, actions, wide }) {
    const titleId = nextId("dlg");
    const previous = document.activeElement;
    const dialog = h("div", { class: "modal" + (wide ? " modal-wide" : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
        h("header", { class: "modal-head" },
            h("h2", { id: titleId }, title),
            h("button", { type: "button", class: "icon-btn", "aria-label": "Close", onclick: () => close() }, "×")),
        h("div", { class: "modal-body" }, body),
        h("div", { class: "modal-error hidden", role: "alert" }),
        h("footer", { class: "modal-foot" }, actions));
    const overlay = h("div", { class: "overlay" }, dialog);
    overlay.addEventListener("mousedown", ev => { if (ev.target === overlay) close(); });
    dialog.addEventListener("keydown", ev => {
        if (ev.key === "Escape") { ev.preventDefault(); close(); }
        if (ev.key === "Tab") {
            const items = Array.from(dialog.querySelectorAll("button:not([disabled]), input:not([disabled])"));
            if (!items.length) return;
            const first = items[0], last = items[items.length - 1];
            if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
            else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
        }
    });
    document.getElementById("modal-root").appendChild(overlay);
    const firstInput = dialog.querySelector(".modal-body input:not([disabled]), .modal-body button:not([disabled])");
    (firstInput || dialog.querySelector(".modal-foot button")).focus();

    function close() {
        overlay.remove();
        if (previous && previous.focus) previous.focus();
    }
    const errorBox = dialog.querySelector(".modal-error");
    return {
        close,
        dialog,
        setError(msg) { errorBox.textContent = msg || ""; errorBox.classList.toggle("hidden", !msg); },
        setBusy(v) { dialog.querySelectorAll(".modal-foot button").forEach(b => { b.disabled = v; }); dialog.classList.toggle("is-busy", v); },
    };
}

/* ------------------------------------------------------------------ rendering */

function renderAlerts() {
    const box = document.getElementById("alerts");
    const c = status.checks;
    const items = [];
    if (!c.samba_installed) {
        items.push(alertBox("danger", "Samba is not installed.", "Run: sudo apt install samba"));
    } else if (!c.registry_enabled) {
        items.push(alertBox("warning", "The Samba registry is not enabled.",
            "Shares are stored in the registry (same as the File Sharing module). This adds 'include = registry' to [global] in smb.conf (a backup is kept).",
            h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: enableRegistry }, "Enable registry")));
    }
    if (!c.proftpd_installed) {
        items.push(alertBox("info", "ProFTPD is not installed - FTP publishing is unavailable.", "Run: sudo apt install proftpd-core (or run install.sh)."));
    } else {
        if (!c.proftpd_include_ok)
            items.push(alertBox("warning", "proftpd.conf does not include /etc/proftpd/conf.d/.", "Add the line 'Include /etc/proftpd/conf.d/' to /etc/proftpd/proftpd.conf."));
        if (c.proftpd_defaultroot_conflict)
            items.push(alertBox("warning", "proftpd.conf has its own DefaultRoot.", `Comment it out so FTP users are jailed in ${status.ftp_root}.`));
    }
    if (c.file_shares && c.file_shares.length)
        items.push(alertBox("info", "Some shares are defined directly in smb.conf and are not managed here:", c.file_shares.join(", ")));
    box.replaceChildren(...items);
}

function alertBox(kind, title, text, action) {
    return h("div", { class: "alert alert-" + kind, role: kind === "danger" ? "alert" : "status" },
        h("div", { class: "alert-text" }, h("strong", {}, title), text ? h("div", {}, text) : null),
        action || null);
}

function accessSummary(share) {
    const parts = [];
    if (share.users.length) parts.push(h("div", {}, h("span", { class: "muted" }, "Users: "), share.users.join(", ")));
    if (share.groups.length) parts.push(h("div", {}, h("span", { class: "muted" }, "Groups: "), share.groups.join(", ")));
    if (!parts.length) parts.push(h("div", { class: "muted" }, "Any authenticated user"));
    if (share.guest_ok) parts.push(h("span", { class: "badge badge-warn" }, "Guest (SMB)"));
    return parts;
}

function renderTable() {
    const rows = document.getElementById("rows");
    const shares = status.shares;
    document.getElementById("loading").classList.add("hidden");
    document.getElementById("tbl").classList.toggle("hidden", !shares.length);
    document.getElementById("empty").classList.toggle("hidden", !!shares.length);
    const canEdit = status.checks.registry_enabled;
    document.getElementById("btn-add").disabled = !canEdit;
    document.getElementById("btn-add-empty").disabled = !canEdit;

    rows.replaceChildren(...shares.map(share => {
        let ftpCell;
        if (!share.ftp) {
            ftpCell = h("span", { class: "badge" }, "Off");
        } else {
            const ro = share.ftp_read_only || share.read_only;
            ftpCell = h("div", {},
                h("span", { class: "badge badge-on" }, ro ? "On · read only" : "On · read/write"),
                share.ftp_mounted ? null : h("div", { class: "text-warn small" }, "Not mounted"),
                h("div", { class: "mono small muted" }, share.ftp_path));
        }
        return h("tr", {},
            h("td", { "data-label": "Name" }, h("div", { class: "strong" }, share.name),
                share.comment ? h("div", { class: "muted small" }, share.comment) : null,
                share.browseable ? null : h("div", { class: "muted small" }, "Hidden")),
            h("td", { "data-label": "Path", class: "mono" }, share.path),
            h("td", { "data-label": "Access" }, accessSummary(share)),
            h("td", { "data-label": "SMB" }, h("span", { class: "badge badge-on" }, share.read_only ? "Read only" : "Read/write")),
            h("td", { "data-label": "FTP" }, ftpCell),
            h("td", { class: "col-actions" },
                h("button", { type: "button", class: "btn btn-link", disabled: !canEdit, onclick: () => shareDialog(share), "aria-label": `Edit ${share.name}` }, "Edit"),
                h("button", { type: "button", class: "btn btn-link btn-danger-link", onclick: () => deleteDialog(share), "aria-label": `Delete ${share.name}` }, "Delete")));
    }));
}

async function refresh() {
    try {
        status = await helper("status");
        renderAlerts();
        renderTable();
    } catch (e) {
        document.getElementById("loading").classList.add("hidden");
        document.getElementById("alerts").replaceChildren(alertBox("danger", "Could not load shares.", e.message,
            h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: refresh }, "Retry")));
    }
}

function reportWarnings(data, successMsg) {
    if (data && data.warnings && data.warnings.length) toast(data.warnings.join(" "), "warning");
    else toast(successMsg);
}

/* ------------------------------------------------------------------ actions */

async function enableRegistry() {
    if (busy) return;
    busy = true;
    try {
        await helper("enable-registry");
        toast("Samba registry enabled.");
        await refresh();
    } catch (e) {
        toast(e.message, "danger");
    } finally {
        busy = false;
    }
}

function shareDialog(share) {
    const isNew = !share;
    const s = share || { name: "", path: "", comment: "", users: [], groups: [], read_only: false, guest_ok: false, browseable: true, ftp: false, ftp_read_only: false };
    const ftpAvailable = status.checks.proftpd_installed;

    const name = textField({ label: "Share name", value: s.name, disabled: !isNew, required: true, placeholder: "e.g. accounting",
        hint: isNew ? "Letters, digits, '.', '_' and '-'. Also used as the FTP folder name." : null });
    const path = textField({ label: "Path", value: s.path, required: true, mono: true, placeholder: "/srv/shares/accounting",
        hint: "Existing directory on this server." });
    const comment = textField({ label: "Description", value: s.comment, placeholder: "Optional" });
    const users = multiSelect({ label: "Allowed users", options: status.users, selected: s.users, placeholder: "Search users…", emptyText: "No local users (UID ≥ 1000)" });
    const groups = multiSelect({ label: "Allowed groups", options: status.groups, selected: s.groups, placeholder: "Search groups…",
        hint: "Leave users and groups empty to allow any authenticated user. FTP uses the same list." });
    const readOnly = toggle({ label: "Read only", checked: s.read_only, hint: "Applies to SMB and FTP." });
    const guest = toggle({ label: "Guest access (SMB only)", checked: s.guest_ok, hint: "FTP never allows anonymous logins." });
    const browse = toggle({ label: "Visible in network browsing", checked: s.browseable });
    const ftpRo = toggle({ label: "FTP read only", checked: s.ftp_read_only, hint: "Read-only over FTP even if SMB is writable." });
    const ftpInfo = h("div", { class: "hint mono" });
    const updateFtpInfo = () => { ftpInfo.textContent = `FTP folder: ${status.ftp_root}/${name.get() || "<name>"}`; };
    const ftp = toggle({
        label: "Also publish over FTP", checked: s.ftp && ftpAvailable,
        hint: ftpAvailable ? "Same users and groups, jailed in " + status.ftp_root + "." : "ProFTPD is not installed.",
        onChange: v => { ftpRo.setDisabled(!v); ftpInfo.classList.toggle("hidden", !v); },
    });
    ftp.setDisabled(!ftpAvailable);
    ftpRo.setDisabled(!(s.ftp && ftpAvailable));
    ftpInfo.classList.toggle("hidden", !(s.ftp && ftpAvailable));
    name.input.addEventListener("input", updateFtpInfo);
    updateFtpInfo();

    const body = h("div", { class: "form" },
        h("div", { class: "grid-2" }, name.el, path.el),
        comment.el,
        h("fieldset", { class: "group" }, h("legend", {}, "Access"),
            h("div", { class: "grid-2" }, users.el, groups.el),
            readOnly.el, guest.el, browse.el),
        h("fieldset", { class: "group group-ftp" }, h("legend", {}, "FTP"),
            ftp.el, ftpRo.el, ftpInfo));

    const save = h("button", { type: "button", class: "btn btn-primary" }, isNew ? "Create share" : "Save");
    const cancel = h("button", { type: "button", class: "btn btn-secondary" }, "Cancel");
    const modal = openModal({ title: isNew ? "Add share" : `Edit share ${s.name}`, body, actions: [cancel, save], wide: true });
    cancel.addEventListener("click", () => modal.close());

    save.addEventListener("click", async () => {
        name.setError(""); path.setError(""); modal.setError("");
        let ok = true;
        if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name.get())) { name.setError("Use 1-64 letters, digits, '.', '_' or '-'."); ok = false; }
        if (!path.get().startsWith("/")) { path.setError("Enter an absolute path, e.g. /srv/shares/data."); ok = false; }
        if (!ok) return;
        modal.setBusy(true);
        try {
            const data = await helper("save", {
                is_new: isNew, name: name.get(), path: path.get(), comment: comment.get(),
                users: users.get(), groups: groups.get(),
                read_only: readOnly.get(), guest_ok: guest.get(), browseable: browse.get(),
                ftp: ftp.get(), ftp_read_only: ftpRo.get(),
            });
            modal.close();
            reportWarnings(data, isNew ? `Share ${data.name} created.` : `Share ${data.name} saved.`);
            await refresh();
        } catch (e) {
            modal.setError(e.message);
            modal.setBusy(false);
            await refresh();
        }
    });
}

function deleteDialog(share) {
    const body = h("div", {},
        h("p", {}, "Delete the share ", h("strong", {}, share.name), "?"),
        h("p", { class: "muted" }, "Files in ", h("span", { class: "mono" }, share.path), " are not touched."));
    const actions = [];
    const cancel = h("button", { type: "button", class: "btn btn-secondary" }, "Cancel");
    actions.push(cancel);
    const run = async (ftpOnly, modal) => {
        modal.setBusy(true);
        try {
            const data = await helper("delete", { name: share.name, ftp_only: ftpOnly });
            modal.close();
            reportWarnings(data, ftpOnly ? `FTP access removed for ${share.name}.` : `Share ${share.name} deleted.`);
        } catch (e) {
            modal.setError(e.message);
            modal.setBusy(false);
        }
        await refresh();
    };
    let ftpOnlyBtn = null;
    if (share.ftp) {
        ftpOnlyBtn = h("button", { type: "button", class: "btn btn-secondary" }, "Remove FTP only");
        actions.push(ftpOnlyBtn);
    }
    const del = h("button", { type: "button", class: "btn btn-danger" }, "Delete share");
    actions.push(del);
    const modal = openModal({ title: "Delete share", body, actions });
    cancel.addEventListener("click", () => modal.close());
    del.addEventListener("click", () => run(false, modal));
    if (ftpOnlyBtn) ftpOnlyBtn.addEventListener("click", () => run(true, modal));
}

function settingsDialog() {
    const st = status.settings;
    const cert = textField({ label: "TLS certificate", value: st.tls_cert, mono: true });
    const key = textField({ label: "TLS private key", value: st.tls_key, mono: true });
    const tls = toggle({
        label: "Require FTPS (explicit TLS)", checked: st.ftp_tls_required,
        hint: "Recommended. Plain FTP sends passwords in clear text.",
        onChange: v => { cert.input.disabled = !v; key.input.disabled = !v; },
    });
    cert.input.disabled = !st.ftp_tls_required;
    key.input.disabled = !st.ftp_tls_required;
    const ports = textField({ label: "Passive port range", value: st.passive_ports, placeholder: "49152-49200", mono: true,
        hint: "Optional. Open this range and port 21 in the firewall." });

    const body = h("div", { class: "form" }, tls.el, h("div", { class: "grid-2" }, cert.el, key.el), ports.el);
    const save = h("button", { type: "button", class: "btn btn-primary" }, "Save");
    const cancel = h("button", { type: "button", class: "btn btn-secondary" }, "Cancel");
    const modal = openModal({ title: "FTP settings", body, actions: [cancel, save], wide: true });
    cancel.addEventListener("click", () => modal.close());
    save.addEventListener("click", async () => {
        ports.setError(""); modal.setError("");
        const p = ports.get();
        const m = p.match(/^(\d{1,5})\s*-\s*(\d{1,5})$/);
        if (p && (!m || +m[1] < 1024 || +m[2] > 65535 || +m[1] > +m[2])) {
            ports.setError("Use a range like 49152-49200 within 1024-65535.");
            return;
        }
        modal.setBusy(true);
        try {
            const data = await helper("settings", {
                ftp_tls_required: tls.get(), tls_cert: cert.get(), tls_key: key.get(), passive_ports: p,
            });
            modal.close();
            reportWarnings(data, "FTP settings saved.");
            await refresh();
        } catch (e) {
            modal.setError(e.message);
            modal.setBusy(false);
        }
    });
}

/* ------------------------------------------------------------------ init */

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("btn-add").addEventListener("click", () => shareDialog(null));
    document.getElementById("btn-add-empty").addEventListener("click", () => shareDialog(null));
    document.getElementById("btn-settings").addEventListener("click", () => { if (status) settingsDialog(); });
    refresh();
});
