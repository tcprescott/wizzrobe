/**
 * The store apps' id: the iOS bundle id and the Android package name. It can't
 * change after the first store upload, and the server's APP_ID setting must
 * match it for community links to open the app. Build with WIZZROBE_APP_ID to
 * override; capacitor.config.ts and the web build both read this.
 */
export const DEFAULT_APP_ID = 'org.speedgaming.wizzrobe'
