// Subscribing MUST happen inside the click gesture: Safari refuses permission prompts outside one.
window.wizWebPush = {
  async _manager() {
    // iOS in a normal tab can return a PushManager, and subscribing through it kills the tab with no JS error.
    // Only the Home Screen app may touch it.
    if (this._isIOS() && !this._standalone()) return null;
    if (window.pushManager) return window.pushManager;
    if ('serviceWorker' in navigator) {
      // .ready never settles if registration failed; don't hang the caller.
      const reg = await Promise.race([
        navigator.serviceWorker.ready,
        new Promise((resolve) => setTimeout(() => resolve(null), 3000)),
      ]);
      if (reg && reg.pushManager) return reg.pushManager;
    }
    return null;
  },

  _applicationServerKey(base64url) {
    return self.wizWebPushKey(base64url);
  },

  _isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent)
      || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  },

  _standalone() {
    return !!navigator.standalone
      || (!!window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  },

  async status() {
    const manager = await this._manager();
    if (!manager) {
      return { supported: false, ios: this._isIOS(), standalone: this._standalone() };
    }
    let endpoint = null;
    try {
      const sub = await manager.getSubscription();
      endpoint = sub ? sub.endpoint : null;
    } catch (e) { /* treated as not subscribed */ }
    return {
      supported: true,
      permission: (typeof Notification !== 'undefined') ? Notification.permission : 'default',
      endpoint: endpoint,
    };
  },

  async subscribe(publicKey) {
    const manager = await this._manager();
    if (!manager) {
      return { error: this._isIOS() && !this._standalone() ? 'ios_needs_install' : 'unsupported' };
    }
    try {
      const sub = await manager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: this._applicationServerKey(publicKey),
      });
      const json = sub.toJSON();
      await self.wizWebPushStore.save({
        endpoint: json.endpoint,
        auth: (json.keys || {}).auth,
        applicationServerKey: publicKey,
      });
      return { subscription: json, userAgent: navigator.userAgent };
    } catch (e) {
      if (typeof Notification !== 'undefined' && Notification.permission === 'denied') {
        return { error: 'permission_denied' };
      }
      if (e && e.name === 'NotAllowedError') {
        return { error: 'permission_dismissed' };
      }
      return { error: String(e) };
    }
  },

  async unsubscribe() {
    const manager = await this._manager();
    if (!manager) return { endpoint: null };
    try {
      const sub = await manager.getSubscription();
      if (!sub) return { endpoint: null };
      const endpoint = sub.endpoint;
      await sub.unsubscribe();
      // Drop the rotation record too, so a later pushsubscriptionchange can't resurrect a device the user turned off.
      await self.wizWebPushStore.clear();
      return { endpoint: endpoint };
    } catch (e) {
      return { endpoint: null, error: String(e) };
    }
  },
};
