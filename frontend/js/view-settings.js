/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Settings: SMB server (incl. macOS/Windows), NFS server, FTP server, change history. */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;
    let sub = "smb";

    function saveBar(onSave) {
        const btn = C.button("Save changes", null, "primary");
        btn.addEventListener("click", async () => {
            btn.disabled = true;
            try { await onSave(); } catch (e) { /* toast shown by SH.action */ } finally { btn.disabled = false; }
        });
        return h("div", { class: "save-bar" }, btn);
    }

    function smbPanel(st) {
        const s = st.state.samba;
        const adOn = st.state.ad.enabled;
        const workgroup = C.text({ label: "Workgroup", value: adOn ? st.state.ad.workgroup : s.workgroup, disabled: adOn, mono: true,
            hint: adOn ? "Set by the Active Directory domain." : "Usually WORKGROUP. Windows clients do not need to match it." });
        const desc = C.text({ label: "Server description", value: s.server_string, hint: "Shown in network browsers. %h is replaced by the host name." });
        const nbName = C.text({ label: "NetBIOS name", value: s.netbios_name, mono: true, placeholder: "Host name (default)", hint: "Up to 15 characters. Leave empty to use the host name." });
        const netbios = C.toggle({ label: "NetBIOS (legacy name service)", checked: s.netbios, hint: "Only needed for very old clients. With it off, Windows finds the server through WS-Discovery." });
        const minProto = C.select({ label: "Minimum protocol", value: s.min_protocol, options: [
            { value: "SMB2_10", label: "SMB 2.1 - Windows 7 and newer (most compatible)" },
            { value: "SMB3_00", label: "SMB 3.0 - Windows 8 / Server 2012 and newer" },
            { value: "SMB3_11", label: "SMB 3.1.1 - Windows 10 / macOS 10.12 and newer (most secure)" },
        ], hint: "SMB1 is always off." });
        const enc = C.select({ label: "Encryption", value: s.encryption, options: [
            { value: "default", label: "Off unless the client asks (fastest)" },
            { value: "desired", label: "Encrypt when the client supports it (recommended)" },
            { value: "required", label: "Always encrypt (refuses older clients)" },
        ] });
        const signing = C.select({ label: "Signing", value: s.signing, options: [
            { value: "default", label: "Samba default" },
            { value: "auto", label: "Offered (auto)" },
            { value: "mandatory", label: "Mandatory" },
        ], hint: "With SMB3 encryption on, signing is implied." });
        const multi = C.toggle({ label: "Multichannel", checked: s.multichannel, hint: "Lets Windows use several network links or RSS queues for one session." });
        const macos = C.toggle({ label: "macOS optimisations (vfs_fruit)", checked: s.macos, hint: "Faster Finder browsing, correct resource forks and Spotlight-friendly metadata. Applies to every SMB share." });
        const model = C.select({ label: "Server icon in Finder", value: s.macos_model, options: [
            { value: "MacSamba", label: "Generic server" }, { value: "Xserve", label: "Xserve" },
            { value: "RackMac", label: "Rack server" }, { value: "TimeCapsule8,119", label: "Time Capsule" },
            { value: "MacPro7,1@ECOLOR=226,226,224", label: "Mac Pro" },
        ].filter(o => /^[A-Za-z0-9,]+$/.test(o.value)) });
        const mdns = C.toggle({ label: "Announce over mDNS / Bonjour", checked: s.mdns, hint: "Server appears in Finder's sidebar and as a Time Machine destination (avahi-daemon)." });
        const wsdd = C.toggle({ label: "Announce over WS-Discovery", checked: s.wsdd, hint: st.checks.wsdd_installed ? "Server appears under Network in Windows Explorer (wsdd-server)." : "Install wsdd-server to appear under Network in Windows Explorer." });
        const homes = C.toggle({ label: "Home folders over SMB", checked: s.homes.enabled, hint: "Every user gets \\\\server\\<username> pointing to their home directory." });
        const homesBrowse = C.toggle({ label: "List home folders when browsing", checked: s.homes.browseable });
        return h("div", { class: "form" },
            C.fieldset("Identity", h("div", { class: "grid-2" }, workgroup.el, nbName.el), desc.el, netbios.el),
            C.fieldset("Security", h("div", { class: "grid-3" }, minProto.el, enc.el, signing.el)),
            C.fieldset("Performance", multi.el),
            C.fieldset("Apple macOS", macos.el, model.el, mdns.el),
            C.fieldset("Windows", wsdd.el),
            C.fieldset("Home folders", homes.el, homesBrowse.el),
            saveBar(() => SH.action(() => SH.call("settings-save", { section: "samba", data: {
                workgroup: adOn ? s.workgroup : workgroup.get(), server_string: desc.get(), netbios_name: nbName.get(), netbios: netbios.get(),
                min_protocol: minProto.get(), encryption: enc.get(), signing: signing.get(), multichannel: multi.get(),
                macos: macos.get(), macos_model: model.get(), mdns: mdns.get(), wsdd: wsdd.get(),
                homes: { enabled: homes.get(), browseable: homesBrowse.get() },
            } }), { success: "SMB settings saved." })));
    }

    function nfsPanel(st) {
        const n = st.state.nfs;
        const v4 = C.toggle({ label: "NFSv4", checked: n.v4, hint: "Recommended. Uses only TCP port 2049." });
        const v3 = C.toggle({ label: "NFSv3", checked: n.v3, hint: "For VMware ESXi older setups and legacy clients. Uses fixed ports 111, 20048 and 32765-32768." });
        const threads = C.text({ label: "Server threads", value: n.threads, mono: true, hint: "8 suits most servers; raise for many busy clients." });
        if (!st.checks.nfs_supported) return C.alert("info", "NFS is not available on this system.", st.checks.nfs_unsupported_reason);
        if (!st.checks.nfs_installed) return C.alert("info", "NFS server is not installed.", "Install it from Overview → Components, or with: sudo apt install nfs-kernel-server");
        return h("div", { class: "form" }, C.fieldset("Versions", v4.el, v3.el), C.fieldset("Performance", threads.el),
            saveBar(() => {
                if (!/^\d{1,3}$/.test(threads.get())) { threads.setError("Enter a number between 1 and 256."); return Promise.resolve(); }
                return SH.action(() => SH.call("settings-save", { section: "nfs", data: { v3: v3.get(), v4: v4.get(), threads: threads.get() } }), { success: "NFS settings saved." });
            }));
    }

    function ftpPanel(st) {
        const f = st.state.ftp;
        if (!st.checks.proftpd_installed) return C.alert("info", "ProFTPD is not installed.", "Install it with: sudo apt install proftpd-core proftpd-mod-crypto");
        const cert = C.text({ label: "Certificate file", value: f.tls_cert, mono: true });
        const key = C.text({ label: "Private key file", value: f.tls_key, mono: true });
        const tlsBox = h("div", { class: "grid-2" + (f.tls_required ? "" : " hidden") }, cert.el, key.el);
        const tls = C.toggle({ label: "Require FTPS (TLS)", checked: f.tls_required, onChange: v => tlsBox.classList.toggle("hidden", !v),
            hint: "Strongly recommended: plain FTP sends passwords in clear text. The default certificate is self-signed; use your own for production." });
        const ports = C.text({ label: "Passive port range", value: f.passive_ports, mono: true, placeholder: "49152-49200", hint: "Set this when the server is behind a firewall or NAT, and open the same range there." });
        const homes = C.toggle({ label: "Home folders over FTP", checked: f.homes, hint: "Users land in /home/<username> after login and cannot see other homes." });
        return h("div", { class: "form" }, C.fieldset("Security", tls.el, tlsBox), C.fieldset("Network", ports.el), C.fieldset("Home folders", homes.el),
            saveBar(() => {
                const p = ports.get();
                const m = p.match(/^(\d{1,5})\s*-\s*(\d{1,5})$/);
                if (p && (!m || +m[1] < 1024 || +m[2] > 65535 || +m[1] > +m[2])) { ports.setError("Use a range like 49152-49200 within 1024-65535."); return Promise.resolve(); }
                return SH.action(() => SH.call("settings-save", { section: "ftp", data: { tls_required: tls.get(), tls_cert: cert.get(), tls_key: key.get(), passive_ports: p, homes: homes.get() } }), { success: "FTP settings saved." });
            }));
    }

    function historyPanel() {
        const box = h("div", {}, h("p", { class: "muted pad" }, "Loading…"));
        SH.call("history").then(data => {
            if (!data.entries.length) { box.replaceChildren(C.empty("No changes recorded yet.")); return; }
            box.replaceChildren(h("table", { class: "tbl" },
                h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Change"), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))),
                h("tbody", {}, data.entries.map((e, i) => h("tr", {},
                    h("td", { class: "mono small" }, e.time.replace("T", " ")),
                    h("td", {}, e.label.replace(/-/g, " "), i === 0 ? C.badge("current", "on") : null),
                    h("td", { class: "col-actions" }, i === 0 ? null : C.button("Restore", async () => {
                        const ok = await C.confirm({ title: "Restore configuration", message: `Bring back every share and setting as they were on ${e.time.replace("T", " ")}? Users, groups and files are not affected.`, confirmLabel: "Restore" });
                        if (ok) SH.action(() => SH.call("history-restore", { id: e.id }), { success: "Configuration restored." }).catch(() => {});
                    }, "link")))))));
        }).catch(e => box.replaceChildren(C.alert("danger", "Could not load history.", e.message)));
        return h("div", {},
            h("p", { class: "muted" }, "Every change is saved. Restore an earlier configuration if something went wrong."),
            h("section", { class: "card" }, box),
            h("div", { class: "save-bar" }, C.button("Re-apply current configuration", () => SH.action(() => SH.call("apply"), { success: "Configuration re-applied." }).catch(() => {}), "secondary",
                { title: "Rewrites Samba, NFS and FTP configuration from ShareHub's state" })));
    }

    SH.views.settings = {
        render(root, st) {
            const panel = h("div", { class: "card card-pad" });
            const tabs = C.tabs({
                items: [{ id: "smb", label: "SMB" }, { id: "nfs", label: "NFS" }, { id: "ftp", label: "FTP" }, { id: "history", label: "History" }],
                active: sub, small: true, onChange: id => { sub = id; draw(); },
            });
            function draw() {
                panel.replaceChildren({ smb: smbPanel, nfs: nfsPanel, ftp: ftpPanel, history: historyPanel }[sub](st));
                panel.classList.toggle("card", sub !== "history");
            }
            draw();
            SH.fill(root, h("div", { class: "toolbar" }, tabs.el), panel);
        },
    };
})();
