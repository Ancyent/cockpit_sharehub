/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* App shell: top-level navigation and rendering on status changes. */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;
    const VIEWS = [
        { id: "overview", label: "Overview" },
        { id: "shares", label: "Shares" },
        { id: "users", label: "Users & Groups" },
        { id: "settings", label: "Settings" },
        { id: "ad", label: "Active Directory" },
    ];

    function currentFromLocation() {
        const path = (cockpit.location.path[0] || "").toLowerCase();
        return VIEWS.some(v => v.id === path) ? path : "overview";
    }

    SH.currentView = currentFromLocation();
    let nav = null;
    const content = () => document.getElementById("content");

    function render() {
        const st = SH.store.status;
        if (!st) return;
        const root = content();
        root.classList.remove("is-loading");
        try {
            SH.views[SH.currentView].render(root, st);
        } catch (e) {
            root.replaceChildren(C.alert("danger", "Something went wrong while drawing this page.", e.message));
            console.error(e);
        }
    }

    function go(id) {
        SH.currentView = id;
        nav.select(id, false);
        cockpit.location.go([id]);
        render();
    }

    document.addEventListener("DOMContentLoaded", async () => {
        nav = C.tabs({ items: VIEWS, active: SH.currentView, onChange: go });
        document.getElementById("nav").replaceChildren(nav.el);
        cockpit.addEventListener("locationchanged", () => {
            const id = currentFromLocation();
            if (id !== SH.currentView) { SH.currentView = id; nav.select(id, false); render(); }
        });
        SH.on("status", render);
        // Reload as soon as the user turns on administrative access in the Cockpit top bar.
        try {
            const su = cockpit.dbus(null, { bus: "internal" }).proxy("cockpit.Superuser", "/superuser");
            su.addEventListener("changed", () => {
                if (su.Current && su.Current !== "none" && !SH.store.status) SH.refresh().catch(() => {});
            });
        } catch (e) { /* older Cockpit: the Retry button still works */ }
        try {
            await SH.refresh();
        } catch (e) {
            content().classList.remove("is-loading");
            content().replaceChildren(C.alert("danger", e.denied ? "Administrative access required." : "Could not load ShareHub.", e.message,
                C.button("Retry", () => location.reload(), "secondary")));
        }
        if (cockpit.transport && cockpit.transport.wait) cockpit.transport.wait(() => {});
    });
})();
