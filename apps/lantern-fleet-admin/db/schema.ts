import { index, integer, sqliteTable, text, uniqueIndex } from 'drizzle-orm/sqlite-core';

const timestamps = {
  createdAt: text('created_at').notNull(),
  updatedAt: text('updated_at').notNull(),
};

export const organizations = sqliteTable('organizations', {
  id: text('id').primaryKey(), name: text('name').notNull(), ...timestamps,
});

export const operatorRoles = sqliteTable('operator_roles', {
  id: text('id').primaryKey(), organizationId: text('organization_id').notNull().references(() => organizations.id),
  authenticatedUserId: text('authenticated_user_id').notNull(), role: text('role', { enum: ['viewer', 'technician', 'fleet_operator', 'release_manager', 'auditor'] }).notNull(),
  scopeType: text('scope_type').notNull(), scopeId: text('scope_id').notNull(), expiresAt: text('expires_at'), ...timestamps,
}, (table) => [uniqueIndex('uidx_operator_roles_assignment').on(table.organizationId, table.authenticatedUserId, table.role, table.scopeType, table.scopeId)]);

export const clusters = sqliteTable('clusters', {
  id: text('id').primaryKey(), organizationId: text('organization_id').notNull().references(() => organizations.id),
  name: text('name').notNull(), regionCode: text('region_code').notNull(), status: text('status').notNull(), ...timestamps,
}, (table) => [index('idx_clusters_organization').on(table.organizationId)]);

export const devices = sqliteTable('devices', {
  id: text('id').primaryKey(), organizationId: text('organization_id').notNull().references(() => organizations.id),
  clusterId: text('cluster_id').references(() => clusters.id), label: text('label').notNull(), hardwareProfile: text('hardware_profile').notNull(),
  lifecycle: text('lifecycle').notNull(), siteLabel: text('site_label'), ownerLabel: text('owner_label'),
  desiredProfileRevision: text('desired_profile_revision'), provisionedAt: text('provisioned_at'), retiredAt: text('retired_at'), ...timestamps,
}, (table) => [index('idx_devices_org_lifecycle').on(table.organizationId, table.lifecycle), index('idx_devices_cluster').on(table.clusterId)]);

export const deviceIdentityAttestations = sqliteTable('device_identity_attestations', {
  id: text('id').primaryKey(), deviceId: text('device_id').notNull().references(() => devices.id),
  identityType: text('identity_type').notNull(), fingerprint: text('fingerprint').notNull(), publicMaterial: text('public_material'),
  generatedAt: text('generated_at').notNull(), verifiedAt: text('verified_at'), supersededAt: text('superseded_at'),
  evidenceBundleId: text('evidence_bundle_id'), ...timestamps,
}, (table) => [uniqueIndex('uidx_device_identity_fingerprint').on(table.identityType, table.fingerprint), index('idx_identities_device').on(table.deviceId)]);

export const observedStates = sqliteTable('observed_states', {
  id: text('id').primaryKey(), deviceId: text('device_id').notNull().references(() => devices.id),
  observedAt: text('observed_at').notNull(), ingestedAt: text('ingested_at').notNull(), sequence: integer('sequence').notNull(),
  clockConfidence: text('clock_confidence').notNull(), sourceFingerprint: text('source_fingerprint').notNull(), stateJson: text('state_json').notNull(), stateHash: text('state_hash').notNull(), signature: text('signature').notNull(),
}, (table) => [uniqueIndex('uidx_observed_device_sequence').on(table.deviceId, table.sequence), index('idx_observed_device_time').on(table.deviceId, table.observedAt)]);

export const desiredProfiles = sqliteTable('desired_profiles', {
  id: text('id').primaryKey(), organizationId: text('organization_id').notNull().references(() => organizations.id),
  name: text('name').notNull(), revision: text('revision').notNull(), profileJson: text('profile_json').notNull(), profileHash: text('profile_hash').notNull(), approvedBy: text('approved_by').notNull(), approvedAt: text('approved_at').notNull(), ...timestamps,
}, (table) => [uniqueIndex('uidx_desired_profile_revision').on(table.organizationId, table.name, table.revision)]);

export const artifactManifests = sqliteTable('artifact_manifests', {
  id: text('id').primaryKey(), name: text('name').notNull(), version: text('version').notNull(), architecture: text('architecture').notNull(),
  sha256: text('sha256').notNull(), sizeBytes: integer('size_bytes').notNull(), sourceUri: text('source_uri').notNull(), license: text('license').notNull(),
  compatibilityJson: text('compatibility_json').notNull(), rollbackArtifactId: text('rollback_artifact_id'), manifestSignature: text('manifest_signature').notNull(), releaseChannel: text('release_channel').notNull(), ...timestamps,
}, (table) => [uniqueIndex('uidx_artifact_name_version_arch').on(table.name, table.version, table.architecture), uniqueIndex('uidx_artifact_sha256').on(table.sha256)]);

export const operations = sqliteTable('operations', {
  id: text('id').primaryKey(), deviceId: text('device_id').notNull().references(() => devices.id),
  type: text('type').notNull(), status: text('status').notNull(), requestedBy: text('requested_by').notNull(), approvedBy: text('approved_by'),
  expectedStateHash: text('expected_state_hash'), desiredRevision: text('desired_revision'), expiresAt: text('expires_at'),
  correlationId: text('correlation_id').notNull(), deviceReceiptHash: text('device_receipt_hash'), failureReason: text('failure_reason'), ...timestamps,
}, (table) => [uniqueIndex('uidx_operations_correlation').on(table.correlationId), index('idx_operations_device_status').on(table.deviceId, table.status)]);

export const evidenceBundles = sqliteTable('evidence_bundles', {
  id: text('id').primaryKey(), deviceId: text('device_id').references(() => devices.id), operationId: text('operation_id').references(() => operations.id),
  evidencePlane: text('evidence_plane').notNull(), claimType: text('claim_type').notNull(), observedAt: text('observed_at').notNull(),
  manifestHash: text('manifest_hash').notNull(), objectPrefix: text('object_prefix').notNull(), retentionClass: text('retention_class').notNull(),
  redactionClass: text('redaction_class').notNull(), fileCount: integer('file_count').notNull(), ...timestamps,
}, (table) => [uniqueIndex('uidx_evidence_manifest_hash').on(table.manifestHash), index('idx_evidence_device_time').on(table.deviceId, table.observedAt)]);

export const auditEvents = sqliteTable('audit_events', {
  id: text('id').primaryKey(), organizationId: text('organization_id').notNull().references(() => organizations.id),
  actorId: text('actor_id').notNull(), actorRole: text('actor_role').notNull(), authorityPlane: text('authority_plane').notNull(),
  action: text('action').notNull(), targetType: text('target_type').notNull(), targetId: text('target_id').notNull(),
  priorRevision: text('prior_revision'), resultRevision: text('result_revision'), outcome: text('outcome').notNull(),
  correlationId: text('correlation_id').notNull(), evidenceBundleId: text('evidence_bundle_id').references(() => evidenceBundles.id), createdAt: text('created_at').notNull(),
}, (table) => [index('idx_audit_org_time').on(table.organizationId, table.createdAt), index('idx_audit_correlation').on(table.correlationId)]);

export const radioObservations = sqliteTable('radio_observations', {
  id: text('id').primaryKey(), observerDeviceId: text('observer_device_id').notNull().references(() => devices.id),
  radioFingerprint: text('radio_fingerprint').notNull(), label: text('label'), observedAt: text('observed_at').notNull(),
  signalDbm: integer('signal_dbm'), signalNoiseRatio: integer('signal_noise_ratio'), hops: integer('hops'), regionCode: text('region_code'),
  routeType: text('route_type').notNull(), trusted: integer('trusted', { mode: 'boolean' }).notNull(), observationSignature: text('observation_signature').notNull(),
}, (table) => [index('idx_radio_observer_time').on(table.observerDeviceId, table.observedAt), index('idx_radio_fingerprint_time').on(table.radioFingerprint, table.observedAt)]);

export const federationLanes = sqliteTable('federation_lanes', {
  id: text('id').primaryKey(), deviceId: text('device_id').notNull().references(() => devices.id), plane: text('plane').notNull(),
  peerFingerprint: text('peer_fingerprint'), laneType: text('lane_type').notNull(), status: text('status').notNull(),
  trustRevision: text('trust_revision'), observedAt: text('observed_at').notNull(), freshnessExpiresAt: text('freshness_expires_at'),
  configurationHash: text('configuration_hash').notNull(), ...timestamps,
}, (table) => [index('idx_federation_device_plane').on(table.deviceId, table.plane)]);

export const aiFeatureStates = sqliteTable('ai_feature_states', {
  id: text('id').primaryKey(), deviceId: text('device_id').notNull().references(() => devices.id), featureKey: text('feature_key').notNull(),
  desiredValueJson: text('desired_value_json').notNull(), desiredRevision: integer('desired_revision').notNull(), observedValueJson: text('observed_value_json'),
  observedRevision: integer('observed_revision'), updatedBy: text('updated_by').notNull(), acknowledgedAt: text('acknowledged_at'), ...timestamps,
}, (table) => [uniqueIndex('uidx_ai_feature_device_key').on(table.deviceId, table.featureKey)]);

export const publicProposalReceipts = sqliteTable('public_proposal_receipts', {
  id: text('id').primaryKey(), deviceId: text('device_id').notNull().references(() => devices.id), proposalId: text('proposal_id').notNull(),
  state: text('state').notNull(), provenanceClass: text('provenance_class').notNull(), locationPrecisionClass: text('location_precision_class').notNull(),
  observedAt: text('observed_at').notNull(), expiresAt: text('expires_at').notNull(), confidenceBasis: text('confidence_basis').notNull(),
  contentHash: text('content_hash').notNull(), receiptHash: text('receipt_hash'), committedReference: text('committed_reference'), updatedAt: text('updated_at').notNull(),
}, (table) => [uniqueIndex('uidx_public_proposal_device_id').on(table.deviceId, table.proposalId), index('idx_public_proposal_state_expiry').on(table.state, table.expiresAt)]);

export const publicQuerySync = sqliteTable('public_query_sync', {
  id: text('id').primaryKey(), deviceId: text('device_id').notNull().references(() => devices.id), scopeHash: text('scope_hash').notNull(),
  localityLabel: text('locality_label').notNull(), topicScope: text('topic_scope').notNull(), transport: text('transport').notNull(),
  lastSuccessfulSyncAt: text('last_successful_sync_at'), validUntil: text('valid_until'), state: text('state').notNull(), updatedAt: text('updated_at').notNull(),
}, (table) => [uniqueIndex('uidx_public_query_device_scope').on(table.deviceId, table.scopeHash), index('idx_public_query_state_valid').on(table.state, table.validUntil)]);
