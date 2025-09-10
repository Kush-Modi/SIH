import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import { ErrorBoundary } from './ErrorBoundary'

const rootEl = document.getElementById('root')!

// Visual mounting indicator
const mountBanner = document.createElement('div')
mountBanner.textContent = 'App mounting...'
mountBanner.style.position = 'fixed'
mountBanner.style.top = '8px'
mountBanner.style.right = '8px'
mountBanner.style.background = '#ecf0f1'
mountBanner.style.border = '1px solid #bdc3c7'
mountBanner.style.padding = '6px 10px'
mountBanner.style.borderRadius = '4px'
mountBanner.style.zIndex = '9999'
document.body.appendChild(mountBanner)

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)

// Remove banner after mount
setTimeout(() => {
  mountBanner.remove()
}, 2000)
