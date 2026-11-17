/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Shares: list + editor (General, Access, SMB, NFS, FTP). */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;
    let filter = "";

    const SQUASH = [
        { value: "root_squash", label: "root_squash - map remote root to nobody (default)" },
        { value: "no_root_squash", label: "no_root_squash - trust remote root (servers only)" },
        { value: "all_squash", label: "all_squash - map every user to the anonymous UID/GID" },
    ];

    function emptyShare() {
        return {
            path: "", comment: "",
            access: { users: [], groups: [], read_only: false },
            smb: { enabled: true, guest_ok: false, browseable: true, time_machine: false, time_machine_max_size: "", recycle: false, recycle_days: 30, recycle_protect: true, recycle_versions: 10, shadow_copies: false, windows_acls: false },
            nfs: { enabled: false, clients: [] },
            ftp: { enabled: false, read_only: false },
            quota: { limit: "" },
            versioning: { snapshots: false, frequent: false, keep_frequent: 8, keep_hourly: 24, keep_daily: 14, keep_weekly: 8, keep_monthly: 6 },
        };
    }

    function usageCell(st, name, s) {
        if (!s.quota.limit) return h("span", { class: "muted" }, "No limit");
        const u = st.quota_usage[name];
        if (!u || u.used === null) return h("div", {}, h("div", {}, "Limit " + s.quota.limit), h("div", { class: "muted small" }, "usage unavailable"));
        const pct = Math.min(100, Math.round((u.used / u.limit) * 100));
        const kind = pct >= 95 ? "danger" : pct >= 80 ? "warn" : "ok";
        const fill = h("div", { class: "meter-fill" });
        fill.style.width = pct + "%"; // CSSOM is allowed by Cockpit's CSP (inline style attributes are not)
        const bar = h("div", { class: "meter meter-" + kind, role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": `${name} usage` }, fill);
        return h("div", { class: "usage" }, bar, h("div", { class: "small" }, `${SH.bytes(u.used)} of ${SH.bytes(u.limit)}`, h("span", { class: "muted" }, ` · ${pct}%`)));
    }

    function principalOptions(list) {
        return list.map(x => ({ value: x.name, label: x.name, meta: x.domain ? "domain" : (x.full_name || "") }));
    }

    function features(s) {
        const out = [];
        if (s.smb.enabled && s.smb.time_machine) out.push(C.badge("Time Machine", "info"));
        if (s.versioning.snapshots) out.push(C.badge("Versions" + (s.versioning.frequent ? " · 15 min" : " · hourly"), "info"));
        if (s.smb.enabled && s.smb.recycle) out.push(C.badge(s.smb.recycle_protect ? "Recycle bin (protected)" : "Recycle bin", "info"));
        if (s.smb.enabled && s.smb.shadow_copies) out.push(C.badge("Previous Versions", "info"));
        if (s.smb.enabled && s.smb.windows_acls) out.push(C.badge("Windows ACLs", "info"));
        if (s.smb.enabled && s.smb.guest_ok) out.push(C.badge("Guest", "warn"));
        return out;
    }

    function accessSummary(s) {
        const a = s.access;
        const lines = [];
        if (a.users.length) lines.push(h("div", {}, h("span", { class: "muted" }, "Users: "), a.users.join(", ")));
        if (a.groups.length) lines.push(h("div", {}, h("span", { class: "muted" }, "Groups: "), a.groups.join(", ")));
        if (!lines.length) lines.push(h("div", { class: "muted" }, "Any authenticated user"));
        if (s.nfs.enabled) lines.push(h("div", { class: "small" }, h("span", { class: "muted" }, "NFS: "), s.nfs.clients.map(c => c.host).join(", ")));
        return lines;
    }

    function render(root, st) {
        const shares = Object.entries(st.state.shares).sort((a, b) => a[0].localeCompare(b[0]));
        const canEdit = st.checks.registry_enabled || !st.checks.samba_installed;
        const search = h("input", { type: "text", class: "input search", placeholder: "Filter shares…", "aria-label": "Filter shares", value: filter });
        const rowsBody = h("tbody");
        const table = h("table", { class: "tbl" },
            h("thead", {}, h("tr", {}, ["Name", "Path", "Protocols", "Access", "Size", "Features"].map(t => h("th", { scope: "col" }, t)), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))),
            rowsBody);
        function fill() {
            const q = filter.toLowerCase();
            const visible = shares.filter(([n, s]) => !q || n.toLowerCase().includes(q) || s.path.toLowerCase().includes(q) || s.comment.toLowerCase().includes(q));
            rowsBody.replaceChildren(...visible.map(([name, s]) => {
                const ftpMounted = st.ftp_mounts.includes(`${st.ftp_root}/${name}`);
                return h("tr", {},
                    h("td", { "data-label": "Name" }, h("div", { class: "strong" }, name), s.comment ? h("div", { class: "muted small" }, s.comment) : null),
                    h("td", { "data-label": "Path", class: "mono small" }, s.path),
                    h("td", { "data-label": "Protocols" }, h("div", { class: "badges" },
                        s.smb.enabled ? C.badge("SMB", "on") : null,
                        s.nfs.enabled ? C.badge("NFS", "on") : null,
                        s.ftp.enabled ? C.badge(ftpMounted ? "FTP" : "FTP · not mounted", ftpMounted ? "on" : "warn") : null,
                        s.access.read_only ? C.badge("Read only") : null)),
                    h("td", { "data-label": "Access" }, accessSummary(s)),
                    h("td", { "data-label": "Size" }, usageCell(st, name, s)),
                    h("td", { "data-label": "Features" }, h("div", { class: "badges" }, features(s))),
                    h("td", { class: "col-actions" },
                        s.versioning.snapshots || (s.smb.enabled && s.smb.recycle) ? C.button("Versions", () => SH.views.versions.open(name), "link", { "aria-label": `Versions of ${name}` }) : null,
                        C.button("Edit", () => editor(name, s), "link", { disabled: !canEdit, "aria-label": `Edit ${name}` }),
                        C.button("Delete", () => remove(name, s), "link", { class: "btn btn-link btn-danger-link", "aria-label": `Delete ${name}` })));
            }));
            if (!visible.length) rowsBody.appendChild(h("tr", {}, h("td", { colspan: "7", class: "muted center" }, "No shares match the filter.")));
        }
        search.addEventListener("input", () => { filter = search.value; fill(); });
        fill();
        const add = C.button("Add share", () => editor(null, emptyShare()), "primary", { disabled: !canEdit });
        SH.fill(root,
            canEdit ? null : C.alert("warning", "Enable the Samba registry first.", "Open the Overview tab to finish the setup."),
            h("div", { class: "toolbar" }, search, add),
            h("section", { class: "card" }, shares.length ? table : C.empty("No shares yet. A share is a folder published over SMB, NFS and/or FTP.", C.button("Add the first share", () => editor(null, emptyShare()), "primary", { disabled: !canEdit }))));
    }

    async function remove(name, s) {
        const ok = await C.confirm({
            title: `Delete share ${name}`,
            message: [`The share stops being published over every protocol.`, h("p", { class: "muted" }, "Files in ", h("span", { class: "mono" }, s.path), " are not deleted.")],
            confirmLabel: "Delete share", danger: true,
        });
        if (ok) SH.action(() => SH.call("share-delete", { name }), { success: `Share ${name} deleted.` }).catch(() => {});
    }

    /* ---------------------------------------------------------------- editor */

    function nfsClientsEditor(clients) {
        const rows = [];
        const box = h("div", { class: "nfs-clients" });
        const list = h("div", { class: "nfs-list" });
        function addRow(c) {
            c = Object.assign({ host: "", rw: true, sync: true, squash: "root_squash", anonuid: "", anongid: "" }, c || {});
            const host = C.text({ label: "Client", value: c.host, placeholder: "10.0.0.0/24, host.lan or *", mono: true });
            const rw = C.toggle({ label: "Read/write", checked: c.rw });
            const sync = C.toggle({ label: "Synchronous writes", checked: c.sync, hint: "Safer; turn off only for scratch data." });
            const anonuid = C.text({ label: "Anonymous UID", value: c.anonuid, mono: true, placeholder: "65534" });
            const anongid = C.text({ label: "Anonymous GID", value: c.anongid, mono: true, placeholder: "65534" });
            const anon = h("div", { class: "grid-2" + (c.squash === "all_squash" ? "" : " hidden") }, anonuid.el, anongid.el);
            const squash = C.select({ label: "User mapping", value: c.squash, options: SQUASH, onChange: v => anon.classList.toggle("hidden", v !== "all_squash") });
            const row = { host, rw, sync, squash, anonuid, anongid };
            const del = C.button("Remove", () => { rows.splice(rows.indexOf(row), 1); el.remove(); }, "link", { class: "btn btn-link btn-danger-link", "aria-label": "Remove this client" });
            const el = h("div", { class: "nfs-row" }, h("div", { class: "grid-2" }, host.el, squash.el), h("div", { class: "row-inline" }, rw.el, sync.el, del), anon);
            rows.push(row);
            list.appendChild(el);
            return host;
        }
        (clients.length ? clients : []).forEach(addRow);
        box.append(list, C.button("Add client", () => addRow().input.focus(), "secondary"));
        return {
            el: box,
            get: () => rows.map(r => ({ host: r.host.get(), rw: r.rw.get(), sync: r.sync.get(), squash: r.squash.get(), anonuid: r.anonuid.get(), anongid: r.anongid.get() })),
            isEmpty: () => rows.length === 0,
            add: addRow,
        };
    }

    function editor(originalName, share) {
        const st = SH.store.status;
        const s = JSON.parse(JSON.stringify(share));
        const isNew = !originalName;
        const ftpReady = st.checks.proftpd_installed;
        const nfsReady = st.checks.nfs_installed && st.checks.nfs_supported;

        // General
        const name = C.text({ label: "Share name", value: originalName || "", required: true, placeholder: "e.g. accounting",
            hint: "Letters, digits, '.', '_' and '-'. This is the SMB share name and the FTP folder name." });
        const path = C.folder({ label: "Folder", value: s.path, required: true, hint: "Existing folder on this server. Use Browse… to pick or create one." });
        const comment = C.text({ label: "Description", value: s.comment, placeholder: "Optional" });
        const quotaHint = h("div", { class: "hint", "aria-live": "polite" });
        const quotaField = C.text({ label: "Size limit", value: s.quota.limit, mono: true, placeholder: "e.g. 500G or 2T (empty = no limit)" });
        quotaField.el.appendChild(quotaHint);
        let quotaOk = null;
        async function checkQuota() {
            const p = path.get();
            if (!p.startsWith("/")) { quotaHint.textContent = "Enforced by the filesystem (ZFS dataset, XFS/ext4 project quota or Btrfs subvolume)."; quotaOk = null; return; }
            quotaHint.textContent = "Checking the filesystem…";
            try {
                const r = await SH.call("quota-check", { path: p });
                quotaOk = r.supported;
                quotaHint.className = "hint" + (r.supported ? "" : " text-warn");
                quotaHint.textContent = r.supported
                    ? `Enforced by ${r.method === "zfs" ? "ZFS refquota" : r.method === "btrfs" ? "a Btrfs qgroup (clients see the whole volume size)" : r.method.toUpperCase() + " project quota"}.`
                    : `Not available here: ${r.reason}`;
            } catch (e) { quotaOk = null; quotaHint.className = "hint"; quotaHint.textContent = e.message; }
        }
        path.input.addEventListener("change", checkQuota);
        path.input.addEventListener("blur", checkQuota);
        const smbOn = C.toggle({ label: "SMB (Windows, macOS, Linux)", checked: s.smb.enabled, onChange: syncTabs });
        const nfsOn = C.toggle({ label: "NFS (Linux, VMware, Proxmox)", checked: s.nfs.enabled && nfsReady, onChange: syncTabs,
            hint: nfsReady ? null : (st.checks.nfs_supported ? "NFS server is not installed - install it from Overview → Components." : st.checks.nfs_unsupported_reason) });
        nfsOn.setDisabled(!nfsReady);
        const ftpOn = C.toggle({ label: "FTP / FTPS", checked: s.ftp.enabled && ftpReady, onChange: syncTabs,
            hint: ftpReady ? null : "ProFTPD is not installed (apt install proftpd-core)." });
        ftpOn.setDisabled(!ftpReady);

        // Access
        const users = C.multiSelect({ label: "Allowed users", options: principalOptions(st.users), selected: s.access.users, placeholder: "Search users…", emptyText: "No users yet - create them under Users & Groups" });
        const groups = C.multiSelect({ label: "Allowed groups", options: principalOptions(st.groups), selected: s.access.groups, placeholder: "Search groups…",
            hint: "Leave both empty to allow any authenticated user. Applies to SMB and FTP." });
        const readOnly = C.toggle({ label: "Read only", checked: s.access.read_only, hint: "For every protocol." });

        // SMB
        const guest = C.toggle({ label: "Guest access", checked: s.smb.guest_ok, hint: "Anyone on the network can connect without a password. Use only for public, non-sensitive data." });
        const browse = C.toggle({ label: "Visible when browsing the network", checked: s.smb.browseable });
        const tmSize = C.text({ label: "Size limit", value: s.smb.time_machine_max_size, placeholder: "e.g. 1T (empty = no limit)", mono: true });
        const tm = C.toggle({ label: "Time Machine backup destination", checked: s.smb.time_machine, onChange: v => tmSize.el.classList.toggle("hidden", !v),
            hint: "Macs can back up here. Use a dedicated share." });
        tmSize.el.classList.toggle("hidden", !s.smb.time_machine);
        const recycleDays = C.text({ label: "Keep deleted files for (days)", value: s.smb.recycle_days, mono: true, hint: "0 keeps them until you remove them." });
        const recycleVersions = C.text({ label: "Versions kept per file", value: s.smb.recycle_versions, mono: true, hint: "When the same file is deleted again, older copies beyond this number are removed. 0 = no limit." });
        const recycleProtect = C.toggle({ label: "Protect deleted files from users", checked: s.smb.recycle_protect,
            hint: "Users and ransomware cannot see, change or purge the recycle bin. Restore files from Shares → Versions." });
        const recycleBox = h("div", { class: "form" }, h("div", { class: "grid-2" }, recycleDays.el, recycleVersions.el), recycleProtect.el);
        const recycle = C.toggle({ label: "Recycle bin", checked: s.smb.recycle, onChange: v => recycleBox.classList.toggle("hidden", !v),
            hint: "Files deleted over SMB are kept in .recycle/<user> inside the share instead of disappearing." });
        recycleBox.classList.toggle("hidden", !s.smb.recycle);
        const shadowHint = st.checks.sanoid_installed || st.checks.zfs_auto_snapshot_installed || st.checks.snapper_installed
            ? "Windows shows snapshots of this folder under Properties → Previous Versions. Needs ZFS or Btrfs snapshots."
            : "Needs ZFS snapshots (sanoid or zfs-auto-snapshot) or Btrfs snapshots (snapper). None of these tools is installed.";
        const shadow = C.toggle({ label: "Previous Versions", checked: s.smb.shadow_copies, hint: shadowHint });
        const acls = C.toggle({ label: "Windows ACLs", checked: s.smb.windows_acls, hint: "Let Windows clients set detailed permissions (stored as extended attributes). Leave off for simple POSIX permissions." });

        // Versions
        const V = s.versioning;
        const verHint = h("div", { class: "hint", "aria-live": "polite" });
        const keepFields = {
            keep_frequent: C.text({ label: "15-minute versions", value: V.keep_frequent, mono: true, hint: "8 = the last 2 hours" }),
            keep_hourly: C.text({ label: "Hourly", value: V.keep_hourly, mono: true, hint: "24 = one per hour for a day" }),
            keep_daily: C.text({ label: "Daily", value: V.keep_daily, mono: true }),
            keep_weekly: C.text({ label: "Weekly", value: V.keep_weekly, mono: true }),
            keep_monthly: C.text({ label: "Monthly", value: V.keep_monthly, mono: true }),
        };
        const frequent = C.toggle({ label: "Every 15 minutes", checked: V.frequent, hint: "Otherwise every hour. At most 15 minutes of work can be lost.",
            onChange: v => keepFields.keep_frequent.el.classList.toggle("hidden", !v) });
        keepFields.keep_frequent.el.classList.toggle("hidden", !V.frequent);
        const verBox = h("div", { class: "form" }, frequent.el, h("div", { class: "label" }, "How many versions to keep"),
            h("div", { class: "grid-3" }, Object.values(keepFields).map(f => f.el)));
        const snapOn = C.toggle({ label: "Keep scheduled versions", checked: V.snapshots, onChange: v => verBox.classList.toggle("hidden", !v),
            hint: "Read-only snapshots of the whole share. Users and ransomware cannot change or delete them; Windows shows them under Previous Versions." });
        verBox.classList.toggle("hidden", !V.snapshots);
        let verOk = null;
        async function checkVersions() {
            const p = path.get();
            if (!p.startsWith("/")) { verHint.textContent = "Needs a ZFS dataset or a Btrfs subvolume."; verOk = null; return; }
            try {
                const r = await SH.call("versions-check", { path: p });
                verOk = r.supported;
                snapOn.setDisabled(!r.supported && !snapOn.get());
                verHint.className = "hint" + (r.supported ? "" : " text-warn");
                verHint.textContent = r.supported ? `Possible here: ${r.method === "zfs" ? "ZFS snapshots" : "Btrfs read-only snapshots"}.` : "Not possible for this folder: " + r.reason;
            } catch (e) { verOk = null; verHint.textContent = e.message; }
        }

        // NFS
        const nfsClients = nfsClientsEditor(s.nfs.clients);
        // FTP
        const ftpRo = C.toggle({ label: "Read only over FTP", checked: s.ftp.read_only, hint: "Even when SMB is writable." });
        const ftpPath = h("p", { class: "muted mono small" });
        const updateFtpPath = () => { ftpPath.textContent = `FTP path: /${name.get() || "<name>"} (inside the FTP jail ${st.ftp_root})`; };
        name.input.addEventListener("input", updateFtpPath);
        updateFtpPath();

        const panels = {
            general: h("div", { class: "form" }, h("div", { class: "grid-2" }, name.el, comment.el), path.el, quotaField.el,
                C.fieldset("Publish over", smbOn.el, nfsOn.el, ftpOn.el)),
            access: h("div", { class: "form" }, h("div", { class: "grid-2" }, users.el, groups.el), readOnly.el,
                C.alert("info", "NFS does not use these lists.", "NFS trusts the client machine and maps users by UID/GID. Control NFS access with the client list on the NFS tab and with folder permissions.")),
            smb: h("div", { class: "form" },
                C.fieldset("Access", guest.el, browse.el),
                C.fieldset("Apple macOS", tm.el, tmSize.el),
                C.fieldset("Data protection", recycle.el, recycleBox, shadow.el),
                C.fieldset("Windows", acls.el)),
            nfs: h("div", { class: "form" }, h("p", { class: "muted" }, "Machines allowed to mount this folder. Each line can have its own options."), nfsClients.el),
            versions: h("div", { class: "form" }, snapOn.el, verHint, verBox,
                C.alert("info", "Deleted files", "Files deleted over SMB are kept by the recycle bin (SMB tab), which works on every filesystem. Restore versions and deleted files from Shares → Versions.")),
            ftp: h("div", { class: "form" }, ftpRo.el, ftpPath,
                h("p", { class: "muted small" }, "FTP users log in with their normal password and are jailed: they only see the shares they have access to. Require FTPS under Settings → FTP.")),
        };
        const tabItems = [
            { id: "general", label: "General" }, { id: "access", label: "Access" },
            { id: "smb", label: "SMB" }, { id: "nfs", label: "NFS" }, { id: "ftp", label: "FTP" }, { id: "versions", label: "Versions" },
        ];
        const panelBox = h("div", { class: "tab-panel", role: "tabpanel" });
        let currentTab = "general";
        const tabs = C.tabs({ items: tabItems, active: "general", small: true, onChange: id => show(id) });
        function show(id) {
            currentTab = id;
            tabs.select(id);
            const off = { smb: !smbOn.get(), nfs: !nfsOn.get(), ftp: !ftpOn.get() }[id];
            panelBox.replaceChildren(off ? C.empty(`${id.toUpperCase()} is off for this share. Turn it on in General.`) : panels[id]);
        }
        function syncTabs() {
            if (nfsOn.get() && nfsClients.isEmpty()) nfsClients.add({ host: "" });
            show(currentTab);
        }
        show("general");
        checkQuota();
        const pathSet = path.set;
        path.set = v => { pathSet(v); checkQuota(); checkVersions(); };
        path.input.addEventListener("change", checkVersions);
        checkVersions();

        const modal = C.formModal({
            title: isNew ? "Add share" : `Edit share ${originalName}`,
            body: h("div", {}, tabs.el, panelBox), size: "lg",
            submitLabel: isNew ? "Create share" : "Save",
            onSubmit: async () => {
                name.setError(""); path.setError("");
                if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name.get())) { show("general"); name.setError("Use 1-64 letters, digits, '.', '_' or '-'."); return false; }
                if (!path.get().startsWith("/")) { show("general"); path.setError("Choose a folder (absolute path)."); return false; }
                if (!smbOn.get() && !nfsOn.get() && !ftpOn.get()) { show("general"); throw new Error("Turn on at least one protocol."); }
                if (quotaField.get() && !/^\d+(\.\d+)?\s*[KMGTPkmgtp]?$/.test(quotaField.get())) { show("general"); quotaField.setError("Use a size like 500G or 2T."); return false; }
                if (quotaField.get() && quotaOk === false) { show("general"); quotaField.setError("This folder does not support size limits - see the note below."); return false; }
                if (recycle.get() && !/^\d{1,4}$/.test(String(recycleDays.get()))) { show("smb"); recycleDays.setError("Enter a number of days."); return false; }
                if (recycle.get() && !/^\d{1,4}$/.test(String(recycleVersions.get()))) { show("smb"); recycleVersions.setError("Enter a number."); return false; }
                for (const f of Object.values(keepFields)) if (snapOn.get() && !/^\d{1,4}$/.test(String(f.get()))) { show("versions"); f.setError("Enter a number."); return false; }
                if (snapOn.get() && verOk === false) { show("versions"); throw new Error("Scheduled versions are not possible for this folder - see the Versions tab."); }
                const payload = {
                    name: name.get(),
                    original_name: originalName,
                    share: {
                        path: path.get(), comment: comment.get(),
                        access: { users: users.get(), groups: groups.get(), read_only: readOnly.get() },
                        smb: { enabled: smbOn.get(), guest_ok: guest.get(), browseable: browse.get(), time_machine: tm.get(), time_machine_max_size: tmSize.get(), recycle: recycle.get(), recycle_days: recycleDays.get() || 0, recycle_protect: recycleProtect.get(), recycle_versions: recycleVersions.get() || 0, shadow_copies: shadow.get(), windows_acls: acls.get() },
                        versioning: Object.assign({ snapshots: snapOn.get(), frequent: frequent.get() }, Object.fromEntries(Object.entries(keepFields).map(([k, f]) => [k, f.get() || 0]))),
                        nfs: { enabled: nfsOn.get(), clients: nfsOn.get() ? nfsClients.get().filter(c => c.host) : [] },
                        ftp: { enabled: ftpOn.get(), read_only: ftpRo.get() },
                        quota: { limit: quotaField.get() },
                    },
                };
                const data = await SH.call("share-save", payload);
                SH.report(data, isNew ? `Share ${data.name} created.` : `Share ${data.name} saved.`);
                await SH.refresh();
            },
        });
        return modal;
    }

    SH.views.shares = { render, editor };
})();
