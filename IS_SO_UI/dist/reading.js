/* Progressive enhancement: ordinary scrolling and readable content work without animation. */
(() => {
  let dispose = () => {};
  window.cleanupReadingFlow = () => dispose();
  window.setupReadingFlow = () => {
    dispose();
    const root = document.documentElement;
    const sections = [...document.querySelectorAll('#project-overview, #project-process, #project-results, #project-usage')];
    if (!sections.length) return;
    const links = [...document.querySelectorAll('.reading-nav a')];
    const progress = document.querySelector('.reading-progress');
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    let observer = null;
    let frame = 0;
    root.classList.add('reading-page');
    const revealAll = () => sections.forEach(section => section.classList.remove('section-awaiting'));
    const configureMotion = () => {
      observer?.disconnect();
      revealAll();
      if (preference.matches || !('IntersectionObserver' in window)) return;
      observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.remove('section-awaiting');
            observer.unobserve(entry.target);
          }
        }
      }, {threshold: 0, rootMargin: '0px 0px -36px 0px'});
      for (const section of sections) {
        section.classList.add('reading-section');
        if (section.getBoundingClientRect().top > window.innerHeight) {
          section.classList.add('section-awaiting');
          observer.observe(section);
        }
      }
    };
    const update = () => {
      frame = 0;
      let active = sections[0];
      for (const section of sections) if (section.getBoundingClientRect().top <= 160) active = section;
      for (const link of links) {
        if (link.hash === '#' + active.id) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      }
      const total = root.scrollHeight - window.innerHeight;
      if (progress) progress.style.transform = `scaleX(${total > 0 ? Math.min(1, Math.max(0, window.scrollY / total)) : 1})`;
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    const onFocus = event => event.target.closest('.reading-section')?.classList.remove('section-awaiting');
    const onLink = event => {
      const link = event.target.closest('a[href^="#project-"]');
      if (link) document.getElementById(link.hash.slice(1))?.classList.remove('section-awaiting');
    };
    configureMotion();
    update();
    window.addEventListener('scroll', onScroll, {passive: true});
    window.addEventListener('resize', onScroll, {passive: true});
    document.addEventListener('focusin', onFocus);
    document.addEventListener('click', onLink);
    preference.addEventListener?.('change', configureMotion);
    dispose = () => {
      observer?.disconnect();
      cancelAnimationFrame(frame);
      revealAll();
      root.classList.remove('reading-page');
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      document.removeEventListener('focusin', onFocus);
      document.removeEventListener('click', onLink);
      preference.removeEventListener?.('change', configureMotion);
      dispose = () => {};
    };
  };
})();
