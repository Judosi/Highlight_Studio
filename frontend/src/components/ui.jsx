import React from 'react'

export function Card({ title, icon, children, className = '' }) {
  return <section className={`card ${className}`}>
    <div className="cardTitle">{icon}{title}</div>
    {children}
  </section>
}

export function Pill({ children }) {
  return <span className="pill">{children}</span>
}

export function Stat({ value, label, icon }) {
  return <div className="statCard">
    <div className="statIcon">{icon}</div>
    <div>
      <b>{value}</b>
      <span>{label}</span>
    </div>
  </div>
}
