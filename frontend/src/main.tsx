import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import './design-system.css'

const root = document.getElementById('root')
if (!root) throw new Error('Roometry root element is missing')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
