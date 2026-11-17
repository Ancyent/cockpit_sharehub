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

    function servicesCard(data) {
        if (!data.systemd) return C.alert("info", "systemd is not running here.", "Service status is unavailable.");
        const rows = data.units.filter(u => u.installed).map(u => {
            const running = u.active === "active";
            return h("tr", {},
                h("td", {}, C.dot(u.active), " ", h("span", { class: "strong" }, u.label), h("div", { class: "muted small mono" }, u.unit)),
                h("td", {}, running ? C.badge("Running", "on") : u.active === "failed" ? C.badge("Failed", "danger") : C.badge("Stopped")),
                h("td", {}, u.enabled === "enabled" ? "Starts at boot" : h("span", { class: "muted" }, "Manual")),
                h("td", { class: "col-actions" },
                    running ? C.button("Restart", () => ctl(u.unit, "restart"), "link") : C.button("Start", () => ctl(u.unit, "enable"), "link"),
                    running ? C.button("Stop", () => ctl(u.unit, "stop"), "link", { class: "btn btn-link btn-danger-link" }) : null));
        });
        const missing = data.units.filter(u => u.installed === false).map(u => u.label);
        return h("div", {},
            h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "Service"), h("th", {}, "State"), h("th", {}, "Boot"), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))), h("tbody", {}, rows)),
            missing.length ? h("p", { class: "muted small pad" }, "Not installed: " + missing.join(", ")) : null);
    }

    async function ctl(unit, action) {
        await SH.action(() => SH.call("service-control", { unit, action }), { success: `${unit}: ${action} done.`, refresh: false }).catch(() => {});
        loadLive();
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

    async function loadLive() {
        if (!liveBox) return;
        try {
            const [svc, conn, fw] = await Promise.all([SH.call("services"), SH.call("connections"), SH.call("firewall-status")]);
            liveBox.services.replaceChildren(servicesCard(svc));
            liveBox.connections.replaceChildren(connectionsCard(conn));
            liveBox.firewall.replaceChildren(firewallCard(fw));
        } catch (e) {
            liveBox.services.replaceChildren(C.alert("danger", "Could not load live status.", e.message));
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
                h("div", { class: "grid-cards" },
                    card("Services", liveBox.services),
                    card("Firewall", liveBox.firewall)),
                card("Live connections", liveBox.connections, C.button("Refresh", loadLive, "link")));
            loadLive();
            clearInterval(pollTimer);
            pollTimer = setInterval(() => { if (!document.hidden && SH.currentView === "overview") loadLive(); }, 15000);
        },
    };
})();
