import { readFileSync, writeFileSync } from 'node:fs';

function replaceOnce(path, before, after) {
  const source = readFileSync(path, 'utf8');
  if (source.split(before).length !== 2) {
    throw new Error(`Expected first-login code was not found once in ${path}`);
  }
  writeFileSync(path, source.replace(before, after));
}

function replaceCount(path, before, after, expectedCount) {
  const source = readFileSync(path, 'utf8');
  if (source.split(before).length - 1 !== expectedCount) {
    throw new Error(`Expected ${expectedCount} user projections in ${path}`);
  }
  writeFileSync(path, source.replaceAll(before, after));
}

replaceOnce(
  '/app/src/modules/auth/auth.service.ts',
  `    if (!user.mustChangePassword) {
      Logger.warn(\`completeFirstLogin called for \${email} but mustChangePassword is already false\`);
    }
    const hashed = await hashPassword(newPassword);
    await this.userStorage.completeFirstLogin(email, hashed);`,
  `    if (!user.mustChangePassword) {
      throw new UnauthorizedException('First-login token already used');
    }
    const hashed = await hashPassword(newPassword);
    const changed = await this.userStorage.completeFirstLogin(email, hashed);
    if (!changed.success) {
      throw new UnauthorizedException('First-login token already used');
    }`,
);

replaceOnce(
  '/app/src/storage/postgres-user.storage.ts',
  `  async completeFirstLogin(email: string, hashedPassword: string): Promise<{ success: boolean }> {
    await this.pool.query(
      \`UPDATE users
          SET password = $2,
              must_change_password = FALSE,
              updated_at = NOW()
        WHERE email = $1\`,
      [email, hashedPassword],
    );
    return { ...SUCCESS };
  }`,
  `  async completeFirstLogin(email: string, hashedPassword: string): Promise<{ success: boolean }> {
    const result = await this.pool.query(
      \`UPDATE users
          SET password = $2,
              must_change_password = FALSE,
              updated_at = NOW()
        WHERE email = $1 AND must_change_password = TRUE\`,
      [email, hashedPassword],
    );
    return { success: result.rowCount === 1 };
  }`,
);

replaceCount(
  '/app/src/storage/postgres-user.storage.ts',
  `    return {
      userId: row.user_id,
      email: row.email,
      password: row.password,
      lastSelectedProviderId: row.last_selected_provider_id,
      mustChangePassword: row.must_change_password,
      contactEmail: row.contact_email,
      emailVerifiedAt: row.email_verified_at,
      roles: row.roles,
      permissions: row.permissions,
      ...row.data,`,
  `    return {
      ...row.data,
      userId: row.user_id,
      email: row.email,
      password: row.password,
      lastSelectedProviderId: row.last_selected_provider_id,
      mustChangePassword: row.must_change_password,
      contactEmail: row.contact_email,
      emailVerifiedAt: row.email_verified_at,
      roles: row.roles,
      permissions: row.permissions,`,
  3,
);
