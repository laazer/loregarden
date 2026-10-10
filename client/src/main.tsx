import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { LazyMotion, MotionConfig, domAnimation } from 'motion/react'
import './index.css'
import { App } from './App.tsx'
import { RootErrorBoundary } from './components/RootErrorBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* `strict` rejects the full `motion.*` components, keeping the bundle to
        `m.*` plus these features. Motion's own default ignores the OS
        reduced-motion setting; "user" honours it, as index.css does for CSS. */}
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <RootErrorBoundary>
          <App />
        </RootErrorBoundary>
      </MotionConfig>
    </LazyMotion>
  </StrictMode>,
)
