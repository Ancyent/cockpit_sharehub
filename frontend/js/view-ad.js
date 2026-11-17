/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Active Directory: join wizard, status, leave. Also the import (migration) wizard. */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;

    function checkList(checks) {
        return h("ul", { class: "checks" }, checks.map(c => h("li", { class: c.ok ? "ok" : "bad" },
            h("span", { class: "check-icon", "aria-hidden": "true" }, c.ok ? "✓" : "!"),
            h("div", {}, h("span", { class: "strong" }, c.name), h("div", { class: "muted small" }, c.detail)))));
    }

    function joinForm(st) {
        const realm = C.text({ label: "Domain (DNS name)", required: true, mono: true, placeholder: "corp.example.com" });
        const workgroup = C.text({ label: "NetBIOS domain name", mono: true, placeholder: "Detected from the domain, e.g. CORP", hint: "The short (pre-Windows 2000) domain name. Leave empty if it is the first part of the DNS name." });
        const backend = C.select({ label: "ID mapping", value: "rid", options: [
            { value: "rid", label: "rid - computed from the Windows SID (recommended, no AD changes)" },
            { value: "ad", label: "ad - read uidNumber/gidNumber from AD (RFC2307 attributes required)" },
        ], hint: "Use the same choice on every Samba server that shares these folders over NFS." });
        const range = C.text({ label: "ID range", value: "10000-999999", mono: true });
        const defDomain = C.toggle({ label: "Use short names", checked: true, hint: "Users log in as 'maria' instead of 'CORP\\maria'. Recommended for a single domain." });
        const shell = C.toggle({ label: "Allow domain users to log in with SSH", checked: false });
        const user = C.text({ label: "Domain admin account", required: true, placeholder: "Administrator", autocomplete: "username" });
        const pass = C.text({ label: "Password", required: true, password: true, autocomplete: "current-password", hint: "Used once to create the computer account. It is not stored." });
        const checksBox = h("div");
        const pre = C.button("Check prerequisites", async () => {
            realm.setError("");
            if (!/^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/.test(realm.get())) { realm.setError("Enter the domain's DNS name, e.g. corp.example.com."); return; }
            checksBox.replaceChildren(h("p", { class: "muted" }, "Checking…"));
            try {
                const data = await SH.call("ad-preflight", { realm: realm.get() });
                checksBox.replaceChildren(checkList(data.checks));
            } catch (e) { checksBox.replaceChildren(C.alert("danger", "Check failed.", e.message)); }
        }, "secondary");
        const join = C.button("Join domain", async () => {
            realm.setError(""); user.setError(""); pass.setError("");
            if (!realm.get()) { realm.setError("Enter the domain."); return; }
            if (!user.get()) { user.setError("Enter an account allowed to join computers."); return; }
            if (!pass.get()) { pass.setError("Enter the password."); return; }
            const ok = await C.confirm({ title: `Join ${realm.get()}`, message: ["This server becomes a member of the domain. Samba switches to domain authentication; local Samba accounts stop working for SMB.", "Existing shares keep working for domain users and groups you add to them."], confirmLabel: "Join domain" });
            if (!ok) return;
            join.disabled = true;
            try {
                await SH.action(() => SH.call("ad-join", {
                    realm: realm.get(), workgroup: workgroup.get(), idmap_backend: backend.get(), idmap_range: range.get(),
                    default_domain: defDomain.get(), login_shell: shell.get(), admin_user: user.get(), admin_password: pass.get(),
                }), { success: `Joined ${realm.get()}.` });
            } catch (e) { /* toast shown */ } finally { join.disabled = false; pass.set(""); }
        }, "primary");
        const missing = st.checks.ad_missing_packages;
        return h("div", { class: "form" },
            missing.length ? C.alert("warning", "Packages missing for Active Directory.", "Install: sudo apt install " + missing.join(" ")) : null,
            C.fieldset("Domain", h("div", { class: "grid-2" }, realm.el, workgroup.el), h("div", {}, pre), checksBox),
            C.fieldset("Identity mapping", h("div", { class: "grid-2" }, backend.el, range.el), defDomain.el, shell.el),
            C.fieldset("Credentials", h("div", { class: "grid-2" }, user.el, pass.el)),
            h("div", { class: "save-bar" }, join));
    }

    function joinedView(st) {
        const box = h("div", {}, h("p", { class: "muted pad" }, "Loading domain status…"));
        SH.call("ad-status").then(s => {
            const row = (k, v) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, v));
            box.replaceChildren(h("table", { class: "tbl kv" }, h("tbody", {},
                row("Domain", s.realm), row("NetBIOS name", s.workgroup),
                row("Domain controller", s.ldap_server_name || "-"),
                row("Time offset", s.time_offset || "-"),
                row("Computer account", s.testjoin ? C.badge("Valid", "on") : C.badge("Problem", "danger")),
                row("Winbind trust", s.trust ? C.badge("OK", "on") : C.badge("Not confirmed", "warn")),
                row("ID mapping", `${s.idmap_backend} (${s.idmap_range})`),
                row("User names", s.default_domain ? "Short (maria)" : "Qualified (DOMAIN\\maria)"))));
        }).catch(e => box.replaceChildren(C.alert("danger", "Could not read domain status.", e.message)));
        const leave = C.button("Leave domain…", () => {
            const user = C.text({ label: "Domain admin account", placeholder: "Administrator" });
            const pass = C.text({ label: "Password", password: true });
            C.formModal({
                title: `Leave ${st.state.ad.realm}`, size: "md", submitLabel: "Leave domain", danger: true,
                body: h("div", { class: "form" }, h("p", {}, "Domain users lose access to every share. Samba goes back to local accounts."),
                    h("p", { class: "muted" }, "With credentials the computer account is removed from AD. Without them, delete it in AD yourself."),
                    h("div", { class: "grid-2" }, user.el, pass.el)),
                onSubmit: async () => {
                    const data = await SH.call("ad-leave", { admin_user: user.get(), admin_password: pass.get() });
                    SH.report(data, "Left the domain.");
                    await SH.refresh();
                },
            });
        }, "secondary", { class: "btn btn-secondary btn-danger-outline" });
        return h("div", {}, h("section", { class: "card" }, box), h("div", { class: "save-bar" }, leave));
    }

    SH.views.ad = {
        render(root, st) {
            SH.fill(root, st.state.ad.enabled
                ? joinedView(st)
                : h("div", {},
                    h("p", { class: "muted" }, "Join this server to an Active Directory domain so people use their domain accounts for SMB and FTP, and shares can be given to domain groups."),
                    h("section", { class: "card card-pad" }, joinForm(st))));
        },
    };

    /* ---------------------------------------------------------------- import wizard */

    SH.views.migration = {
        async open() {
            let data;
            try { data = await SH.call("migrate-detect"); } catch (e) { SH.toast(e.message, "danger"); return; }
            const shareBoxes = data.shares.map(s => ({
                s, cb: C.checkbox({
                    label: s.name, checked: true,
                    description: [s.path, s.nfs_clients ? ` · NFS clients: ${s.nfs_clients}` : "", s.ftp ? " · FTP" : "",
                        s.dropped_params.length ? ` · not kept: ${s.dropped_params.join(", ")}` : ""].join(""),
                }),
            }));
            const nfsBoxes = data.nfs_only.map(n => ({ n, cb: C.checkbox({ label: n.path, checked: true, description: `${n.clients} client(s) from ${n.source}` }) }));
            if (!shareBoxes.length && !nfsBoxes.length && !data.smbftp) { SH.toast("Nothing to import."); return; }
            const body = h("div", { class: "form" },
                h("p", {}, "These shares and exports were created outside ShareHub. Imported items are managed here from now on."),
                shareBoxes.length ? C.fieldset("SMB shares", shareBoxes.map(x => x.cb.el)) : null,
                nfsBoxes.length ? C.fieldset("NFS-only exports", nfsBoxes.map(x => x.cb.el)) : null,
                data.smbftp ? C.alert("info", "Settings from the cockpit-smbftp prototype will be imported too.", "FTP publishing, FTPS and home folders.") : null,
                data.file_sharing_installed ? C.alert("warning", "The 45Drives File Sharing module is installed.", "After importing, remove it (sudo apt remove cockpit-file-sharing) so two tools do not edit the same shares.") : null);
            C.formModal({
                title: "Import existing shares", body, size: "md", submitLabel: "Import",
                onSubmit: async () => {
                    const res = await SH.call("migrate-import", {
                        shares: shareBoxes.filter(x => x.cb.get()).map(x => x.s.name),
                        nfs_paths: nfsBoxes.filter(x => x.cb.get()).map(x => x.n.path),
                    });
                    SH.report(res, `Imported ${SH.plural(res.imported.length, "share")}.`);
                    await SH.refresh();
                },
            });
        },
    };
})();
