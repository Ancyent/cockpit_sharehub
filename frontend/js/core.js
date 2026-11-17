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
