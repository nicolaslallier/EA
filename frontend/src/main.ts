import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import { applyStoredTheme } from './lib/theme'
import { createAppRouter } from './router'

applyStoredTheme()
createApp(App).use(createAppRouter()).mount('#app')
