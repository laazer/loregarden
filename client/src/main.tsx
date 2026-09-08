import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { App } from './App.tsx'
import { RootErrorBoundary } from './components/RootErrorBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </StrictMode>,
)
