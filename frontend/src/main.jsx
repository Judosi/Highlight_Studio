import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './app/App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './styles/index.css'
import './styles/theme.css'
import './styles/redesign.css'
import './styles/workspace.css'
import './styles/studio-v2.css'
import './styles/studio-v3.css'
import './styles/studio-final.css'
import './features/shorts/shorts.css'

createRoot(document.getElementById('root')).render(<ErrorBoundary><App /></ErrorBoundary>)
