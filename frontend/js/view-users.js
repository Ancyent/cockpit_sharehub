/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Users & Groups: local accounts with one password for the system and Samba. */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;
    let sub = "users";

    const SMB_BADGE = {
        active: ["Samba: active", "on"],
        disabled: ["Samba: disabled", "warn"],
        none: ["No Samba password", ""],
    };

    function usedBy(st, kind, name) {
        return Object.entries(st.state.shares).filter(([, s]) => s.access[kind].includes(name)).map(([n]) => n);
    }

    function localGroups(st) {
        return st.groups.filter(g => !g.domain).map(g => g.name);
    }

    function passwordPair(required) {
        const p1 = C.text({ label: "Password", password: true, required, hint: "At least 8 characters." });
        const p2 = C.text({ label: "Repeat password", password: true, required });
        return {
            els: [p1.el, p2.el],
            check() {
                p1.setError(""); p2.setError("");
                if (p1.get().length < 8) { p1.setError("Use at least 8 characters."); return null; }
                if (p1.get() !== p2.get()) { p2.setError("The passwords do not match."); return null; }
                return p1.get();
            },
        };
    }

    function userDialog(st, user) {
        const isNew = !user;
        const u = user || { name: "", full_name: "", groups: [], login_shell: false };
        const name = C.text({ label: "Username", value: u.name, required: true, disabled: !isNew, placeholder: "e.g. maria.pop",
            hint: isNew ? "Lower-case letters, digits, '_' or '-'." : null });
        const full = C.text({ label: "Full name", value: u.full_name, placeholder: "Optional" });
        const groups = C.multiSelect({ label: "Groups", options: localGroups(st), selected: u.groups.filter(g => localGroups(st).includes(g)), placeholder: "Search groups…", emptyText: "No groups yet - create one on the Groups tab" });
        const login = C.toggle({ label: "Allow shell login (SSH)", checked: u.login_shell, hint: "File sharing (SMB, FTP) works without it. Leave off for normal users." });
        const pw = isNew ? passwordPair(true) : null;
        const smb = isNew ? C.toggle({ label: "Create Samba account", checked: true, hint: "Needed for SMB access. Uses the same password." }) : null;
        const body = h("div", { class: "form" }, h("div", { class: "grid-2" }, name.el, full.el), groups.el,
            pw ? h("div", { class: "grid-2" }, pw.els) : null, smb ? smb.el : null, login.el);
        C.formModal({
            title: isNew ? "Add user" : `Edit user ${u.name}`, body, size: "md", submitLabel: isNew ? "Create user" : "Save",
            onSubmit: async () => {
                if (isNew && !/^[a-z_][a-z0-9_-]{0,31}$/.test(name.get())) { name.setError("Lower-case letters, digits, '_' or '-', starting with a letter."); return false; }
                const password = pw ? pw.check() : null;
                if (pw && !password) return false;
                const payload = { name: name.get(), full_name: full.get(), groups: groups.get(), login_shell: login.get() };
                if (isNew) Object.assign(payload, { password, smb: smb.get() });
                const data = await SH.call(isNew ? "user-create" : "user-update", payload);
                SH.report(data, isNew ? `User ${data.name} created.` : `User ${data.name} saved.`);
                await SH.refresh();
            },
        });
    }

    function passwordDialog(u) {
        const pw = passwordPair(true);
        const smb = C.toggle({ label: "Also set the Samba password", checked: true, hint: "Keeps SMB and FTP/SSH logins on the same password." });
        C.formModal({
            title: `Set password for ${u.name}`, size: "md", submitLabel: "Set password",
            body: h("div", { class: "form" }, h("div", { class: "grid-2" }, pw.els), smb.el),
            onSubmit: async () => {
                const password = pw.check();
                if (!password) return false;
                const data = await SH.call("user-password", { name: u.name, password, smb: smb.get() });
                SH.report(data, `Password updated for ${u.name}.`);
                await SH.refresh();
            },
        });
    }

    function groupDialog(st, group) {
        const isNew = !group;
        const name = C.text({ label: "Group name", value: group ? group.name : "", required: true, disabled: !isNew, placeholder: "e.g. accounting" });
        const members = C.multiSelect({ label: "Members", options: st.users.filter(u => !u.domain).map(u => ({ value: u.name, label: u.name, meta: u.full_name })), selected: group ? group.members : [], placeholder: "Search users…" });
        C.formModal({
            title: isNew ? "Add group" : `Edit group ${group.name}`, size: "md", submitLabel: isNew ? "Create group" : "Save",
            body: h("div", { class: "form" }, name.el, members.el),
            onSubmit: async () => {
                if (isNew && !/^[a-z_][a-z0-9_-]{0,31}$/.test(name.get())) { name.setError("Lower-case letters, digits, '_' or '-'."); return false; }
                const data = await SH.call(isNew ? "group-create" : "group-update", { name: name.get(), members: members.get() });
                SH.report(data, isNew ? `Group ${data.name} created.` : `Group ${data.name} saved.`);
                await SH.refresh();
            },
        });
    }

    function usersTable(st) {
        const local = st.users.filter(u => !u.domain);
        if (!local.length) return C.empty("No local users yet.", C.button("Add the first user", () => userDialog(st, null), "primary"));
        return h("table", { class: "tbl" },
            h("thead", {}, h("tr", {}, ["User", "Groups", "Samba", "Used by shares"].map(t => h("th", { scope: "col" }, t)), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))),
            h("tbody", {}, local.map(u => {
                const used = usedBy(st, "users", u.name);
                let [label, kind] = SMB_BADGE[u.smb];
                if (u.smb === "none" && used.length) { label = "No Samba password - cannot use SMB"; kind = "warn"; }
                return h("tr", {},
                    h("td", { "data-label": "User" }, h("div", { class: "strong" }, u.name), h("div", { class: "muted small" }, u.full_name || ""), u.login_shell ? C.badge("Shell login") : null),
                    h("td", { "data-label": "Groups" }, u.groups.join(", ") || h("span", { class: "muted" }, "-")),
                    h("td", { "data-label": "Samba" }, C.badge(label, kind)),
                    h("td", { "data-label": "Used by" }, used.join(", ") || h("span", { class: "muted" }, "-")),
                    h("td", { class: "col-actions" },
                        C.button("Edit", () => userDialog(st, u), "link"),
                        C.button("Password", () => passwordDialog(u), "link"),
                        u.smb === "active" ? C.button("Disable Samba", () => SH.action(() => SH.call("user-smb", { name: u.name, action: "disable" }), { success: `Samba access disabled for ${u.name}.` }).catch(() => {}), "link", { class: "btn btn-link btn-danger-link" }) : null,
                        u.smb === "disabled" ? C.button("Enable Samba", () => SH.action(() => SH.call("user-smb", { name: u.name, action: "enable" }), { success: `Samba access enabled for ${u.name}.` }).catch(() => {}), "link") : null));
            })));
    }

    function groupsTable(st) {
        const local = st.groups.filter(g => !g.domain);
        if (!local.length) return C.empty("No groups yet. Groups make it easy to give several users access to a share.", C.button("Add the first group", () => groupDialog(st, null), "primary"));
        return h("table", { class: "tbl" },
            h("thead", {}, h("tr", {}, ["Group", "Members", "Used by shares"].map(t => h("th", { scope: "col" }, t)), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))),
            h("tbody", {}, local.map(g => {
                const used = usedBy(st, "groups", g.name);
                return h("tr", {},
                    h("td", { "data-label": "Group" }, h("div", { class: "strong" }, g.name), h("div", { class: "muted small" }, "GID " + g.gid)),
                    h("td", { "data-label": "Members" }, g.members.join(", ") || h("span", { class: "muted" }, "-")),
                    h("td", { "data-label": "Used by" }, used.join(", ") || h("span", { class: "muted" }, "-")),
                    h("td", { class: "col-actions" },
                        C.button("Edit", () => groupDialog(st, g), "link"),
                        C.button("Delete", async () => {
                            const ok = await C.confirm({ title: `Delete group ${g.name}`, message: "Members keep their accounts; only the group is removed.", confirmLabel: "Delete group", danger: true });
                            if (ok) SH.action(() => SH.call("group-delete", { name: g.name }), { success: `Group ${g.name} deleted.` }).catch(() => {});
                        }, "link", { class: "btn btn-link btn-danger-link", disabled: used.length > 0, title: used.length ? "Remove it from its shares first" : null })));
            })));
    }

    SH.views.users = {
        render(root, st) {
            const panel = h("div");
            const tabs = C.tabs({
                items: [{ id: "users", label: "Users" }, { id: "groups", label: "Groups" }], active: sub, small: true,
                onChange: id => { sub = id; draw(); },
            });
            const addBtn = h("div");
            function draw() {
                panel.replaceChildren(h("section", { class: "card" }, sub === "users" ? usersTable(st) : groupsTable(st)));
                addBtn.replaceChildren(sub === "users"
                    ? C.button("Add user", () => userDialog(st, null), "primary")
                    : C.button("Add group", () => groupDialog(st, null), "primary"));
            }
            draw();
            const domain = st.state.ad.enabled
                ? C.alert("info", `Joined to ${st.state.ad.realm}.`, `${st.users.filter(u => u.domain).length} domain users and ${st.groups.filter(g => g.domain).length} domain groups are available in the share access lists. Manage them in Active Directory.`)
                : null;
            SH.fill(root,
                C.alert("info", "One password for everything.",
                    "Passwords set here apply to the system account (FTP, SSH) and to Samba at the same time. If you change a password elsewhere (for example in Cockpit's Accounts page), set it again here so SMB stays in sync."),
                domain,
                h("div", { class: "toolbar" }, tabs.el, addBtn),
                panel);
        },
    };
})();
