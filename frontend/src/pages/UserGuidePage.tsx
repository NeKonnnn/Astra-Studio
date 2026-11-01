import React from 'react';
import StaticHtmlDocFrame from './StaticHtmlDocFrame';

/** Руководство пользователя — HTML из public/static/user_documentation. */
export default function UserGuidePage() {
  const src = `${process.env.PUBLIC_URL || ''}/static/user_documentation/user-guide.html`;
  return <StaticHtmlDocFrame src={src} title="AstraChat — руководство пользователя" />;
}
