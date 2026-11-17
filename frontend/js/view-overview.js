/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Overview: setup checks, counters, services, live connections, firewall. */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;
    let pollTimer = null;
    let liveBox = null;

    function setupAlerts(st) {
        const c = st.checks;
        const out = [];
        if (!c.samba_installed) {
            out.push(C.alert("danger", "Samba is not installed.", "Install it with: sudo apt install samba"));
        } else if (!c.registry_enabled) {
            out.push(C.alert("warning", "Finish the setup: enable the Samba registry.",
                "ShareHub stores Samba settings in the registry. This adds 'include = registry' to [global] in /etc/samba/smb.conf (a backup is kept as smb.conf.sharehub.bak).",
                C.button("Enable registry", () => SH.action(() => SH.call("enable-registry"), { success: "Samba registry enabled." }), "primary", { class: "btn btn-primary btn-sm" })));
        }
        if (c.unmanaged_shares.length || c.file_shares.length) {
            const names = c.unmanaged_shares.concat(c.file_shares);
            out.push(C.alert("info", "Existing shares found that ShareHub does not manage yet.",
                names.join(", ") + (c.file_shares.length ? " (shares defined directly in smb.conf can only be imported by hand)." : "."),
                c.unmanaged_shares.length ? C.button("Import…", () => SH.views.migration.open(), "secondary", { class: "btn btn-secondary btn-sm" }) : null));
        }
        if (c.proftpd_installed && !c.proftpd_include_ok)
            out.push(C.alert("warning", "proftpd.conf does not include /etc/proftpd/conf.d/.", "Add 'Include /etc/proftpd/conf.d/' to /etc/proftpd/proftpd.conf, or reinstall the package."));
        if (c.proftpd_defaultroot_conflict)
            out.push(C.alert("warning", "proftpd.conf sets its own DefaultRoot.", `Comment it out so FTP users are jailed in ${st.ftp_root}.`));
        return out;
    }

    function counters(st) {
        const shares = Object.values(st.state.shares);
        const count = p => shares.filter(s => s[p].enabled).length;
        const local = st.users.filter(u => !u.domain);
        const card = (value, label, sub) => h("div", { class: "stat" }, h("div", { class: "stat-value" }, String(value)), h("div", { class: "stat-label" }, label), sub ? h("div", { class: "stat-sub" }, sub) : null);
        return h("div", { class: "stats" },
            card(shares.length, "Shares", `${count("smb")} SMB · ${count("nfs")} NFS · ${count("ftp")} FTP`),
            card(local.length, "Local users", `${local.filter(u => u.smb === "active").length} with Samba access`),
            card(st.state.ad.enabled ? st.state.ad.realm : "Not joined", "Active Directory", st.state.ad.enabled ? st.state.ad.workgroup : "Local users only"),
            card(st.checks.samba_version.replace(/^Version\s*/, "") || "-", "Samba version", "v" + st.version + " ShareHub"));
    }

    const STATUS = {
        ok: ["ok", "Ready"],
        attention: ["danger", "Needs attention"],
        warning: ["warn", "Check configuration"],
        absent: ["off", "Not installed"],
    };
    const expanded = new Set();

    function summary(f) {
        const missing = f.packages.filter(g => !g.satisfied).map(g => g.packages.map(p => p.name).join(" or "));
        const stopped = f.services.filter(s => s.installed && s.active !== "active").map(s => s.unit);
        const failing = f.checks.filter(c => !c.ok).map(c => c.label);
        if (missing.length) return (f.in_use ? "In use, but missing: " : "Optional. Needs: ") + missing.join(", ");
        if (f.in_use && stopped.length) return `In use, but ${stopped.join(", ")} ${stopped.length === 1 ? "is" : "are"} not running`;
        if (failing.length) return failing.join("; ");
        const versions = f.packages.flatMap(g => g.packages.filter(p => p.installed).slice(0, 1).map(p => `${p.name} ${p.installed.replace(/^\d+:/, "").replace(/[-+~].*$/, "")}`));
        const running = f.services.length && f.services.every(s => s.active === "active") ? " · running" : "";
        return (versions.join(", ") || "Configured") + running + (f.in_use ? "" : " · not used yet");
    }

    async function installPackages(f) {
        const list = f.installable.join(" ");
        const ok = await C.confirm({ title: `Install ${f.label}`, message: [`ShareHub will run: apt-get install ${list}`, "This can take a few minutes. Keep this page open."], confirmLabel: "Install" });
        if (!ok) return;
        const m = C.modal({ title: `Installing ${list}…`, body: h("div", { class: "busy-box" }, h("span", { class: "spinner", "aria-hidden": "true" }), h("p", {}, "Downloading and installing packages. Do not close this page.")), actions: [], persistent: true });
        try {
            const data = await SH.call("install-packages", { packages: f.installable });
            m.close();
            SH.report(data, `Installed ${list}.`);
        } catch (e) {
            m.close();
            SH.toast(e.message, "danger");
        }
        await SH.refresh().catch(() => {});
    }

    async function fix(action, label) {
        await SH.action(() => SH.call("health-fix", { action }), { success: `${label}: fixed.`, refresh: false }).catch(() => {});
        await SH.refresh().catch(() => {});
    }

    function featureDetails(f) {
        const pkgRows = f.packages.flatMap(g => g.packages.map((p, i) => h("tr", {},
            h("td", { class: "mono" }, p.name, g.packages.length > 1 && i > 0 ? h("span", { class: "muted small" }, " (alternative)") : null),
            h("td", {}, p.installed ? C.badge("Installed " + p.installed, "on") : p.candidate ? C.badge("Available " + p.candidate) : C.badge("Not available on this system", "warn")))));
        const svcRows = f.services.map(s => h("tr", {},
            h("td", { class: "mono" }, C.dot(s.active), " ", s.unit),
            h("td", {}, s.installed === false ? C.badge("Not installed") : s.active === null ? C.badge("Unknown") : C.badge(s.active === "active" ? "Running" : s.active, s.active === "active" ? "on" : "warn"),
                s.enabled === "enabled" ? h("span", { class: "muted small" }, " · starts at boot") : null),
            h("td", { class: "col-actions" }, s.installed ? [
                s.active === "active" ? C.button("Restart", () => ctl(s.unit, "restart"), "link") : C.button("Start", () => ctl(s.unit, "enable"), "link"),
                s.active === "active" && !f.core ? C.button("Stop", () => ctl(s.unit, "stop"), "link", { class: "btn btn-link btn-danger-link" }) : null] : null)));
        const checkRows = f.checks.map(c => h("tr", {},
            h("td", {}, h("span", { class: c.ok ? "check-ok" : "check-bad", "aria-hidden": "true" }, c.ok ? "✓ " : "! "), c.label),
            h("td", { class: "small" + (c.ok ? " muted" : "") }, c.detail || ""),
            h("td", { class: "col-actions" }, c.fix ? C.button("Fix", () => fix(c.fix, c.label), "link") : null)));
        return h("div", { class: "feature-details" },
            f.note ? h("p", { class: "muted small" }, f.note) : null,
            pkgRows.length ? h("table", { class: "tbl tbl-compact" }, h("thead", {}, h("tr", {}, h("th", {}, "Package"), h("th", {}, "State"))), h("tbody", {}, pkgRows)) : null,
            svcRows.length ? h("table", { class: "tbl tbl-compact" }, h("thead", {}, h("tr", {}, h("th", {}, "Service"), h("th", {}, "State"), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))), h("tbody", {}, svcRows)) : null,
            checkRows.length ? h("table", { class: "tbl tbl-compact" }, h("thead", {}, h("tr", {}, h("th", {}, "Check"), h("th", {}, "Result"), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))), h("tbody", {}, checkRows)) : null);
    }

    function componentsCard(data) {
        const order = { attention: 0, warning: 1, ok: 2, absent: 3 };
        const feats = data.features.slice().sort((a, b) => (order[a.status] - order[b.status]) || (b.in_use - a.in_use));
        const rows = feats.map(f => {
            const [kind, text] = STATUS[f.status];
            const open = expanded.has(f.id);
            const detailsId = "feat-" + f.id;
            const stoppedUnits = f.services.filter(s => s.installed && s.active !== "active");
            const actions = [];
            if (f.installable.length) actions.push(C.button("Install", () => installPackages(f), f.in_use || f.core ? "primary" : "secondary", { class: "btn btn-sm " + (f.in_use || f.core ? "btn-primary" : "btn-secondary") }));
            if (f.in_use && stoppedUnits.length && !f.installable.length) actions.push(C.button("Start", async () => { for (const s of stoppedUnits) await ctl(s.unit, "enable"); }, "secondary", { class: "btn btn-sm btn-secondary" }));
            f.checks.filter(c => !c.ok && c.fix).slice(0, 1).forEach(c => actions.push(C.button("Fix", () => fix(c.fix, c.label), "secondary", { class: "btn btn-sm btn-secondary" })));
            const toggle = h("button", { type: "button", class: "btn btn-link", "aria-expanded": String(open), "aria-controls": detailsId }, open ? "Hide details" : "Details");
            const details = h("div", { id: detailsId, class: open ? "" : "hidden" }, open ? featureDetails(f) : null);
            toggle.addEventListener("click", () => {
                if (expanded.has(f.id)) expanded.delete(f.id); else expanded.add(f.id);
                const now = expanded.has(f.id);
                toggle.setAttribute("aria-expanded", String(now));
                toggle.textContent = now ? "Hide details" : "Details";
                details.classList.toggle("hidden", !now);
                if (now && !details.firstChild) details.appendChild(featureDetails(f));
            });
            return h("div", { class: "feature feature-" + f.status },
                h("div", { class: "feature-row" },
                    h("span", { class: "dot dot-" + (kind === "warn" ? "warn" : kind), "aria-hidden": "true" }),
                    h("div", { class: "feature-main" },
                        h("div", {}, h("span", { class: "strong" }, f.label), " ", C.badge(text, kind === "off" ? "" : kind === "ok" ? "on" : kind), f.in_use ? C.badge("In use", "info") : null),
                        h("div", { class: "muted small" }, summary(f))),
                    h("div", { class: "feature-actions" }, actions, toggle)),
                details);
        });
        const attention = data.features.filter(f => f.status === "attention").length;
        return h("div", {},
            !data.systemd ? h("p", { class: "muted small pad" }, "systemd is not running here - service states are unknown.") : null,
            h("div", { class: "pad muted small" }, attention ? `${SH.plural(attention, "component")} need${attention === 1 ? "s" : ""} attention.` : "Everything that is in use is installed and running."),
            h("div", { class: "features" }, rows));
    }

    function connectionsCard(data) {
        const smb = data.smb;
        const sessions = smb.sessions.length
            ? h("table", { class: "tbl" },
                h("thead", {}, h("tr", {}, ["User", "Client", "Protocol", "Encryption", "Shares", ""].map(t => h("th", {}, t)))),
                h("tbody", {}, smb.sessions.map(s => h("tr", {},
                    h("td", { class: "strong" }, s.user),
                    h("td", {}, s.machine, h("div", { class: "muted small mono" }, s.address)),
                    h("td", {}, s.protocol || "-"),
                    h("td", {}, s.encryption ? C.badge(s.encryption, "on") : h("span", { class: "muted" }, "none")),
                    h("td", {}, smb.shares.filter(t => t.pid === s.pid).map(t => t.share).join(", ") || "-"),
                    h("td", { class: "col-actions" }, C.button("Disconnect", () => disconnect(s), "link", { class: "btn btn-link btn-danger-link" }))))))
            : h("p", { class: "muted pad" }, "No SMB sessions.");
        return h("div", {},
            h("h3", { class: "sub-head" }, "SMB ", h("span", { class: "muted small" }, `${smb.sessions.length} sessions · ${smb.open_files} open files`)),
            sessions,
            h("h3", { class: "sub-head" }, "NFS (v4 clients)"),
            data.nfs.length ? h("ul", { class: "plain-list" }, data.nfs.map(c => h("li", {}, h("span", { class: "mono" }, c.address), " ", h("span", { class: "muted" }, c.name)))) : h("p", { class: "muted pad" }, "No NFSv4 clients."),
            h("h3", { class: "sub-head" }, "FTP"),
            data.ftp.length ? h("ul", { class: "plain-list mono small" }, data.ftp.map(l => h("li", {}, l))) : h("p", { class: "muted pad" }, "No FTP sessions."));
    }

    async function disconnect(s) {
        const ok = await C.confirm({ title: "Disconnect session", message: `Disconnect ${s.user} (${s.machine})? Unsaved work in open files may be lost.`, confirmLabel: "Disconnect", danger: true });
        if (!ok) return;
        await SH.action(() => SH.call("disconnect", { pid: s.pid }), { success: "Session disconnected.", refresh: false }).catch(() => {});
        loadLive();
    }

    function firewallCard(fw) {
        const req = fw.required.map(r => h("li", {}, h("span", { class: "mono" }, `${r.port}/${r.proto}`), " ", h("span", { class: "muted" }, r.label)));
        let status;
        if (!fw.backend) status = h("p", { class: "muted" }, "No ufw or firewalld found. If another firewall protects this server, open the ports below there.");
        else if (!fw.active) status = h("p", { class: "muted" }, `${fw.backend} is installed but not active.`);
        else status = h("p", {}, `${fw.backend} is active.`);
        return h("div", { class: "pad" }, status,
            fw.required.length ? h("div", {}, h("p", { class: "label" }, "Ports used by the protocols you have turned on:"), h("ul", { class: "plain-list cols" }, req)) : h("p", { class: "muted" }, "No protocol is in use yet."),
            fw.backend && fw.active && fw.required.length ? C.button("Open these ports", async () => {
                const ok = await C.confirm({ title: "Open firewall ports", message: `Allow ${fw.required.length} port rules from any address in ${fw.backend}? Restrict them to your LAN in ${fw.backend} afterwards if needed.`, confirmLabel: "Open ports" });
                if (ok) { await SH.action(() => SH.call("firewall-open"), { success: "Firewall ports opened.", refresh: false }).catch(() => {}); loadLive(); }
            }, "secondary") : null);
    }

    async function loadHealth() {
        if (!liveBox) return;
        try {
            liveBox.services.replaceChildren(componentsCard(await SH.call("health")));
        } catch (e) {
            liveBox.services.replaceChildren(C.alert("danger", "Could not check components.", e.message));
        }
    }

    async function loadLive() {
        if (!liveBox) return;
        try {
            const [conn, fw] = await Promise.all([SH.call("connections"), SH.call("firewall-status")]);
            liveBox.connections.replaceChildren(connectionsCard(conn));
            liveBox.firewall.replaceChildren(firewallCard(fw));
        } catch (e) {
            liveBox.connections.replaceChildren(C.alert("danger", "Could not load live status.", e.message));
        }
    }

    SH.views.overview = {
        render(root, st) {
            const card = (title, body, extra) => h("section", { class: "card" }, h("header", { class: "card-head" }, h("h2", {}, title), extra || null), body);
            liveBox = {
                services: h("div", {}, h("p", { class: "muted pad" }, "Loading…")),
                connections: h("div", {}, h("p", { class: "muted pad" }, "Loading…")),
                firewall: h("div", {}, h("p", { class: "muted pad" }, "Loading…")),
            };
            SH.fill(root,
                h("div", { class: "alerts" }, setupAlerts(st)),
                counters(st),
                card("Components", liveBox.services, C.button("Check again", loadHealth, "link")),
                h("div", { class: "grid-cards" },
                    card("Live connections", liveBox.connections, C.button("Refresh", loadLive, "link")),
                    card("Firewall", liveBox.firewall)));
            loadHealth();
            loadLive();
            clearInterval(pollTimer);
            pollTimer = setInterval(() => { if (!document.hidden && SH.currentView === "overview") loadLive(); }, 15000);
        },
    };
})();
