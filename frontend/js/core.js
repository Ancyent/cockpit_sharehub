/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* ShareHub core: helper IPC, DOM helper, events, toasts, theme sync. */
"use strict";

const SH = window.SH = { views: {}, store: { status: null } };
SH.HELPER = "/usr/lib/sharehub/sharehub-helper";

/* ------------------------------------------------------------------ helper IPC */

SH.call = function (command, payload) {
    return new Promise((resolve, reject) => {
        cockpit.spawn([SH.HELPER, command], { superuser: "require", err: "message" })
            .input(payload ? JSON.stringify(payload) : "")
            .then(output => {
                let reply;
                try {
                    reply = JSON.parse(output);
                } catch (e) {
                    reject(new Error("Invalid response from the ShareHub helper: " + output.slice(0, 300)));
                    return;
                }
                if (reply.ok) resolve(reply.data);
                else reject(new Error(reply.error));
            })
            .catch(ex => {
                const denied = ex.problem === "access-denied" || ex.problem === "not-authorized";
                const err = new Error(denied
                    ? "Administrative access is required. Click \"Limited access\" in the Cockpit top bar to turn it on."
                    : (ex.message || String(ex)));
                err.denied = denied;
                reject(err);
            });
    });
};

/* ------------------------------------------------------------------ events */

const listeners = {};
SH.on = (event, fn) => { (listeners[event] = listeners[event] || []).push(fn); };
SH.emit = (event, data) => (listeners[event] || []).forEach(fn => fn(data));

SH.refresh = async function () {
    SH.store.status = await SH.call("status");
    SH.emit("status", SH.store.status);
    return SH.store.status;
};

/* ------------------------------------------------------------------ DOM */

SH.h = function (tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") el.className = value;
        else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
        else el.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
        if (child === null || child === undefined || child === false) continue;
        el.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
    }
    return el;
};

/* replaceChildren that skips null/false entries (conditional content). */
SH.fill = (root, ...children) => root.replaceChildren(...children.flat().filter(c => c !== null && c !== undefined && c !== false));

let uid = 0;
SH.id = prefix => `${prefix || "sh"}-${++uid}`;

SH.toast = function (message, kind) {
    const el = document.getElementById("toast");
    el.replaceChildren(...[].concat(message).map(m => SH.h("div", {}, m)));
    el.className = "toast toast-" + (kind || "success");
    clearTimeout(SH.toast.timer);
    SH.toast.timer = setTimeout(() => el.classList.add("hidden"), kind === "warning" || kind === "danger" ? 10000 : 4000);
};

/* Show a success message, or the helper's warnings if there are any. */
SH.report = function (data, success) {
    const warnings = ((data && data.warnings) || []).filter(w => !/^systemd is not running/.test(w));
    if (warnings.length) SH.toast(warnings, "warning");
    else SH.toast(success);
};

/* Run an async action with error toast + optional refresh. */
SH.action = async function (fn, { success, refresh = true } = {}) {
    try {
        const data = await fn();
        if (success !== undefined) SH.report(data, success);
        if (refresh) await SH.refresh();
        return data;
    } catch (e) {
        SH.toast(e.message, "danger");
        if (refresh) SH.refresh().catch(() => {});
        throw e;
    }
};

SH.bytes = function (n) {
    if (n === null || n === undefined) return "?";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)) + " " + units[i];
};

/* ------------------------------------------------------------------ features */

/* One answer per feature, computed by the helper for every screen:
 * off | impossible | needs_install | needs_setup | ready. */
/* Optional features the admin can turn on or off (SMB itself is always on). */
SH.FEATURES = [
    { id: "nfs", label: "NFS server", about: "Publish shares to Linux, VMware and Proxmox over NFS.", stops: "nfs-server" },
    { id: "ftp", label: "FTP / FTPS server", about: "Publish shares and home folders over FTP and FTPS (ProFTPD).", stops: "proftpd" },
    { id: "mdns", label: "macOS discovery (Bonjour)", about: "Macs find the server in Finder and as a Time Machine destination." },
    { id: "wsdd", label: "Windows discovery (WS-Discovery)", about: "Windows finds the server under Network." },
    { id: "netbios", label: "NetBIOS name service", about: "Legacy name service, only for very old clients." },
    { id: "ad", label: "Active Directory", about: "Join a Windows domain; domain users and groups get access." },
    { id: "recycle", label: "Recycle bin", about: "Keep files deleted over SMB so they can be restored.", stops: "sharehub-recycle.timer" },
    { id: "quota", label: "Size limits", about: "A maximum size per share, enforced by the filesystem." },
    { id: "snapshots", label: "Scheduled versions", about: "Read-only versions of a share every 15 minutes or hourly (ZFS or Btrfs).", stops: "sharehub-snapshot.timer" },
    { id: "versions", label: "Previous Versions from external snapshots", about: "Show snapshots made by sanoid, zfs-auto-snapshot or snapper in Windows." },
];

SH.feature = function (id) {
    const f = SH.store.status && SH.store.status.features && SH.store.status.features[id];
    return f || { state: "ready", reason: null, install: [] };
};
SH.featureOn = id => SH.feature(id).state !== "off";
SH.featureReady = id => SH.feature(id).state === "ready";

/* Install packages with a blocking progress dialog; resolves true on success. */
SH.installPackages = async function (packages, label) {
    const list = packages.join(" ");
    const ok = await SH.controls.confirm({ title: `Install ${label || list}`, message: [`ShareHub will run: apt-get install ${list}`, "This can take a few minutes. Keep this page open."], confirmLabel: "Install" });
    if (!ok) return false;
    const m = SH.controls.modal({ title: `Installing ${list}…`, body: SH.h("div", { class: "busy-box" }, SH.h("span", { class: "spinner", "aria-hidden": "true" }), SH.h("p", {}, "Downloading and installing packages. Do not close this page.")), actions: [], persistent: true });
    try {
        const data = await SH.call("install-packages", { packages });
        m.close();
        SH.report(data, `Installed ${list}.`);
        await SH.refresh().catch(() => {});
        return true;
    } catch (e) {
        m.close();
        SH.toast(e.message, "danger");
        return false;
    }
};

/* Open the Components dashboard with one feature expanded. */
SH.showComponent = function (id) {
    SH.focusComponent = id;
    document.querySelectorAll("#modal-root .overlay").forEach(o => o.remove());
    SH.currentView = "overview";
    cockpit.location.go(["overview"]);
    SH.emit("status", SH.store.status);
};

SH.reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

SH.plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/* ------------------------------------------------------------------ theme (follows Cockpit) */

SH.applyTheme = function () {
    let style = "auto";
    try {
        style = localStorage.getItem("shell:style") || "auto";
    } catch (e) { /* storage unavailable */ }
    const dark = style === "dark" || (style === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("theme-dark", dark);
};
SH.applyTheme();
window.addEventListener("storage", ev => { if (ev.key === "shell:style") SH.applyTheme(); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", SH.applyTheme);
