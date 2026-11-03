import type { CapacitorConfig } from '@capacitor/cli'
import { DEFAULT_APP_ID } from './src/native/appId'

const appId = process.env.WIZZROBE_APP_ID || DEFAULT_APP_ID

const config: CapacitorConfig = {
  appId,
  appName: 'Wizzrobe',
  webDir: 'dist',
  backgroundColor: '#17120D',
  ios: {
    contentInset: 'never',
  },
  android: {
    allowMixedContent: false,
  },
  plugins: {
    PushNotifications: {
      presentationOptions: ['badge', 'sound', 'alert'],
    },
  },
}

export default config
