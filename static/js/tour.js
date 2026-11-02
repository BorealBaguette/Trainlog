// A short guided tour of the navbar, from the welcome card on an empty map, the
// one-time offer after a new account's first trips, or About → Take the tour.
// driver.js is fetched only when a tour starts: nothing loads on ordinary pages.
(function () {
    const DRIVER_JS = 'https://cdn.jsdelivr.net/npm/driver.js@1.3.1/dist/driver.js.iife.js';
    const DRIVER_CSS = 'https://cdn.jsdelivr.net/npm/driver.js@1.3.1/dist/driver.css';
    const SEEN_KEY = 'trainlogTourSeen';

    let loading = null;
    function loadDriver() {
        if (window.driver && window.driver.js) return Promise.resolve();
        if (loading) return loading;
        loading = new Promise((resolve, reject) => {
            const css = document.createElement('link');
            css.rel = 'stylesheet';
            css.href = DRIVER_CSS;
            document.head.appendChild(css);
            const js = document.createElement('script');
            js.src = DRIVER_JS;
            js.onload = resolve;
            js.onerror = reject;
            document.head.appendChild(js);
        });
        return loading;
    }

    function markSeen() {
        try { localStorage.setItem(SEEN_KEY, '1'); } catch (e) {}
    }

    window.trainlogTourSeen = function () {
        try { return !!localStorage.getItem(SEEN_KEY); } catch (e) { return false; }
    };

    window.startTrainlogTour = async function () {
        const t = window.TOUR_I18N;
        if (!t) return;
        try {
            await loadDriver();
        } catch (e) {
            return;  // the CDN is unreachable: no tour, nothing else breaks
        }
        markSeen();
        document.querySelectorAll('.dropdown-menu.show').forEach(menu => {
            const toggle = menu.parentElement && menu.parentElement.querySelector('[data-bs-toggle="dropdown"]');
            if (toggle) bootstrap.Dropdown.getOrCreateInstance(toggle).hide();
        });

        // Below the xl breakpoint the links sit behind the toggler: open it so the
        // steps have something to point at, and fold it back afterwards.
        const toggler = document.querySelector('.navbar-toggler');
        const collapse = document.getElementById('navbarNavDropdown');
        const folded = toggler && collapse && getComputedStyle(toggler).display !== 'none' && !collapse.classList.contains('show');
        if (folded) {
            await new Promise(resolve => {
                collapse.addEventListener('shown.bs.collapse', resolve, { once: true });
                bootstrap.Collapse.getOrCreateInstance(collapse).show();
            });
        }

        const steps = [
            ['map', t.mapTitle, t.mapText],
            ['new', t.newTitle, t.newText],
            ['trips', t.tripsTitle, t.tripsText],
            ['stats', t.statsTitle, t.statsText],
            ['coverage', t.coverageTitle, t.coverageText],
            ['community', t.communityTitle, t.communityText],
            ['settings', t.settingsTitle, t.settingsText],
        ]
            .map(([name, title, description]) => ({ element: document.querySelector(`[data-tour="${name}"]`), title, description }))
            .filter(step => step.element && step.element.offsetParent !== null)
            .map(({ element, title, description }) => ({
                element,
                popover: { title, description, side: folded ? 'left' : 'bottom', align: folded ? 'center' : 'start' },
            }));
        if (!steps.length) return;

        window.driver.js.driver({
            showProgress: true,
            progressText: '{{current}} / {{total}}',
            nextBtnText: t.next,
            prevBtnText: t.prev,
            doneBtnText: t.done,
            popoverClass: 'trainlog-tour',
            steps,
            onDestroyed: () => {
                if (folded) bootstrap.Collapse.getOrCreateInstance(collapse).hide();
            },
        }).drive();
    };
})();
