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
        const MODELS = [
            { value: "MacSamba", label: "File server (generic)", icon: () => SH.icons.server(), about: "A generic network server. A good default for any file server." },
            { value: "Xserve", label: "Rack server, 1U", icon: () => SH.icons.rack1u(), about: "A thin rack-mounted server. Suits servers in a data-center rack." },
            { value: "RackMac", label: "Server rack", icon: () => SH.icons.rack(), about: "A full server rack. Suits larger storage systems." },
            { value: "TimeCapsule8,119", label: "Backup appliance (Time Capsule)", icon: () => SH.icons.backup(), about: "A backup disk. Suits a server used mainly as a Time Machine destination." },
            { value: "MacPro", label: "Tower workstation", icon: () => SH.icons.tower(), about: "A desktop tower. Suits a small office server under a desk." },
            { value: "iMac", label: "All-in-one desktop", icon: () => SH.icons.desktop(), about: "A desktop computer with a display." },
        ];
        const preview = h("div", { class: "icon-preview" });
        const drawPreview = o => {
            if (!o) { preview.replaceChildren(); return; }
            preview.replaceChildren(
                h("div", { class: "icon-preview-art", "aria-hidden": "true" }, SH.icons[{ MacSamba: "server", Xserve: "rack1u", RackMac: "rack", "TimeCapsule8,119": "backup", MacPro: "tower", iMac: "desktop" }[o.value] || "server"](56)),
                h("div", {},
                    h("div", { class: "strong" }, o.label, " ", h("span", { class: "muted mono small" }, `fruit:model = ${o.value}`)),
                    h("div", {}, o.about),
                    h("div", { class: "hint" }, "Macs show this server with Apple's matching device icon in the Finder sidebar (Locations / Network) and in Go → Connect to Server. "
                        + "The drawing here is a simplified preview. It needs \"Announce over mDNS / Bonjour\"; Macs cache icons, so the change can take a reconnect or a Finder restart. Windows and Linux are not affected.")));
        };
        const known = MODELS.some(m => m.value === s.macos_model);
        if (!known) MODELS.push({ value: s.macos_model, label: s.macos_model, icon: () => SH.icons.server(), about: "Custom model identifier." });
        const model = C.select({ label: "How Macs show this server in Finder", value: s.macos_model, options: MODELS, onDescribe: drawPreview });
        const mdns = C.toggle({ label: "Announce over mDNS / Bonjour", checked: s.mdns, hint: "Server appears in Finder's sidebar and as a Time Machine destination (avahi-daemon)." });
        const wsdd = C.toggle({ label: "Announce over WS-Discovery", checked: s.wsdd, hint: "Server appears under Network in Windows Explorer. Without it, Windows still reaches shares as \\\\server\\share." });
        const homes = C.toggle({ label: "Home folders over SMB", checked: s.homes.enabled, hint: "Every user gets \\\\server\\<username> pointing to their home directory." });
        const homesBrowse = C.toggle({ label: "List home folders when browsing", checked: s.homes.browseable });
        return h("div", { class: "form" },
            C.fieldset("Identity", h("div", { class: "grid-2" }, workgroup.el, nbName.el), desc.el, netbios.el),
            C.fieldset("Security", h("div", { class: "grid-3" }, minProto.el, enc.el, signing.el)),
            C.fieldset("Performance", multi.el),
            C.fieldset("Apple macOS", macos.el, mdns.el, s.mdns ? C.featureNote("mdns", null, { compact: true }) : null, model.el, preview),
            C.fieldset("Windows", wsdd.el, s.wsdd ? C.featureNote("wsdd", null, { compact: true }) : null),
            C.fieldset("Home folders", homes.el, homesBrowse.el),
            saveBar(() => SH.action(() => SH.call("settings-save", { section: "samba", data: {
                workgroup: adOn ? s.workgroup : workgroup.get(), server_string: desc.get(), netbios_name: nbName.get(), netbios: netbios.get(),
                min_protocol: minProto.get(), encryption: enc.get(), signing: signing.get(), multichannel: multi.get(),
                macos: macos.get(), macos_model: model.get(), mdns: mdns.get(), wsdd: wsdd.get(),
                homes: { enabled: homes.get(), browseable: homesBrowse.get() },
            } }), { success: "SMB settings saved." })));
    }

    function featuresPanel(st) {
        const rows = SH.FEATURES.map(f => {
            const state = SH.feature(f.id);
            const on = state.state !== "off";
            const toggle = C.toggle({ label: f.label, checked: on, hint: f.about });
            const note = on ? C.featureNote(f.id, state, { compact: true }) : null;
            const badge = !on ? C.badge("Off", "off") : state.state === "ready" ? C.badge("Ready", "on")
                : C.badge({ impossible: "Not possible here", needs_install: "Needs packages", needs_setup: "Needs setup" }[state.state] || state.state,
                    state.state === "needs_install" ? "info" : state.state === "needs_setup" ? "warn" : "");
            return { f, toggle, on, el: h("div", { class: "feature-setting" + (on ? "" : " feature-off") },
                h("div", { class: "feature-setting-row" }, toggle.el, badge), note) };
        });
        const save = async () => {
            const choice = Object.fromEntries(rows.map(r => [r.f.id, r.toggle.get()]));
            const stopping = rows.filter(r => r.on && !r.toggle.get() && r.f.stops).map(r => r.f.stops);
            if (stopping.length) {
                const ok = await C.confirm({ title: "Turn features off",
                    message: [`ShareHub will stop and disable: ${stopping.join(", ")}.`, "Their settings are kept; turning the feature back on starts them again."],
                    confirmLabel: "Turn off", danger: true });
                if (!ok) return;
            }
            await SH.action(() => SH.call("features-set", { features: choice }), { success: "Features saved." });
        };
        return h("div", { class: "form" },
            h("p", { class: "muted" }, "Choose what ShareHub manages on this server. A feature that is off disappears from every screen; one that cannot work here is shown greyed out with the reason."),
            h("div", { class: "feature-settings" }, rows.map(r => r.el)),
            h("div", { class: "save-bar" },
                C.button("Run setup again", () => SH.views.wizard.open(), "secondary"),
                C.button("Save changes", () => save().catch(() => {}), "primary")));
    }

    function nfsPanel(st) {
        const n = st.state.nfs;
        const v4 = C.toggle({ label: "NFSv4", checked: n.v4, hint: "Recommended. Uses only TCP port 2049." });
        const v3 = C.toggle({ label: "NFSv3", checked: n.v3, hint: "For VMware ESXi older setups and legacy clients. Uses fixed ports 111, 20048 and 32765-32768." });
        const threads = C.text({ label: "Server threads", value: n.threads, mono: true, hint: "8 suits most servers; raise for many busy clients." });
        const form = h("div", { class: "form" }, C.fieldset("Versions", v4.el, v3.el), C.fieldset("Performance", threads.el));
        if (!SH.featureReady("nfs")) return C.greyed(form, C.featureNote("nfs"));
        return h("div", { class: "form" }, form,
            saveBar(() => {
                if (!/^\d{1,3}$/.test(threads.get())) { threads.setError("Enter a number between 1 and 256."); return Promise.resolve(); }
                return SH.action(() => SH.call("settings-save", { section: "nfs", data: { v3: v3.get(), v4: v4.get(), threads: threads.get() } }), { success: "NFS settings saved." });
            }));
    }

    function ftpPanel(st) {
        const f = st.state.ftp;
        const cert = C.text({ label: "Certificate file", value: f.tls_cert, mono: true });
        const key = C.text({ label: "Private key file", value: f.tls_key, mono: true });
        const tlsBox = h("div", { class: "grid-2" + (f.tls_required ? "" : " hidden") }, cert.el, key.el);
        const tls = C.toggle({ label: "Require FTPS (TLS)", checked: f.tls_required, onChange: v => tlsBox.classList.toggle("hidden", !v),
            hint: "Strongly recommended: plain FTP sends passwords in clear text. The default certificate is self-signed; use your own for production." });
        const ports = C.text({ label: "Passive port range", value: f.passive_ports, mono: true, placeholder: "49152-49200", hint: "Set this when the server is behind a firewall or NAT, and open the same range there." });
        const homes = C.toggle({ label: "Home folders over FTP", checked: f.homes, hint: "Users land in /home/<username> after login and cannot see other homes." });
        const form = h("div", { class: "form" }, C.fieldset("Security", tls.el, tlsBox, C.featureNote("ftps", null, { compact: true })), C.fieldset("Network", ports.el), C.fieldset("Home folders", homes.el));
        if (!SH.featureReady("ftp")) return C.greyed(form, C.featureNote("ftp"));
        return h("div", { class: "form" }, form,
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
            const items = [
                { id: "smb", label: "SMB" },
                SH.featureOn("nfs") ? { id: "nfs", label: "NFS", greyed: !SH.featureReady("nfs") } : null,
                SH.featureOn("ftp") ? { id: "ftp", label: "FTP", greyed: !SH.featureReady("ftp") } : null,
                { id: "features", label: "Features" },
                { id: "history", label: "History" },
            ].filter(Boolean);
            if (!items.some(i => i.id === sub)) sub = "smb";
            const tabs = C.tabs({ items, active: sub, small: true, onChange: id => { sub = id; draw(); } });
            function draw() {
                panel.replaceChildren({ smb: smbPanel, nfs: nfsPanel, ftp: ftpPanel, features: featuresPanel, history: historyPanel }[sub](st));
                panel.classList.toggle("card", sub !== "history");
            }
            draw();
            SH.fill(root, h("div", { class: "toolbar" }, tabs.el), panel);
        },
    };
})();
