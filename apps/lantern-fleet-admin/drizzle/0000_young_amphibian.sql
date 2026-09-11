CREATE TABLE `ai_feature_states` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text NOT NULL,
	`feature_key` text NOT NULL,
	`desired_value_json` text NOT NULL,
	`desired_revision` integer NOT NULL,
	`observed_value_json` text,
	`observed_revision` integer,
	`updated_by` text NOT NULL,
	`acknowledged_at` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_ai_feature_device_key` ON `ai_feature_states` (`device_id`,`feature_key`);--> statement-breakpoint
CREATE TABLE `artifact_manifests` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`version` text NOT NULL,
	`architecture` text NOT NULL,
	`sha256` text NOT NULL,
	`size_bytes` integer NOT NULL,
	`source_uri` text NOT NULL,
	`license` text NOT NULL,
	`compatibility_json` text NOT NULL,
	`rollback_artifact_id` text,
	`manifest_signature` text NOT NULL,
	`release_channel` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_artifact_name_version_arch` ON `artifact_manifests` (`name`,`version`,`architecture`);--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_artifact_sha256` ON `artifact_manifests` (`sha256`);--> statement-breakpoint
CREATE TABLE `audit_events` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`actor_id` text NOT NULL,
	`actor_role` text NOT NULL,
	`authority_plane` text NOT NULL,
	`action` text NOT NULL,
	`target_type` text NOT NULL,
	`target_id` text NOT NULL,
	`prior_revision` text,
	`result_revision` text,
	`outcome` text NOT NULL,
	`correlation_id` text NOT NULL,
	`evidence_bundle_id` text,
	`created_at` text NOT NULL,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`evidence_bundle_id`) REFERENCES `evidence_bundles`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `idx_audit_org_time` ON `audit_events` (`organization_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `idx_audit_correlation` ON `audit_events` (`correlation_id`);--> statement-breakpoint
CREATE TABLE `clusters` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`name` text NOT NULL,
	`region_code` text NOT NULL,
	`status` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `idx_clusters_organization` ON `clusters` (`organization_id`);--> statement-breakpoint
CREATE TABLE `desired_profiles` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`name` text NOT NULL,
	`revision` text NOT NULL,
	`profile_json` text NOT NULL,
	`profile_hash` text NOT NULL,
	`approved_by` text NOT NULL,
	`approved_at` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_desired_profile_revision` ON `desired_profiles` (`organization_id`,`name`,`revision`);--> statement-breakpoint
CREATE TABLE `device_identity_attestations` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text NOT NULL,
	`identity_type` text NOT NULL,
	`fingerprint` text NOT NULL,
	`public_material` text,
	`generated_at` text NOT NULL,
	`verified_at` text,
	`superseded_at` text,
	`evidence_bundle_id` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_device_identity_fingerprint` ON `device_identity_attestations` (`identity_type`,`fingerprint`);--> statement-breakpoint
CREATE INDEX `idx_identities_device` ON `device_identity_attestations` (`device_id`);--> statement-breakpoint
CREATE TABLE `devices` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`cluster_id` text,
	`label` text NOT NULL,
	`hardware_profile` text NOT NULL,
	`lifecycle` text NOT NULL,
	`site_label` text,
	`owner_label` text,
	`desired_profile_revision` text,
	`provisioned_at` text,
	`retired_at` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`cluster_id`) REFERENCES `clusters`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `idx_devices_org_lifecycle` ON `devices` (`organization_id`,`lifecycle`);--> statement-breakpoint
CREATE INDEX `idx_devices_cluster` ON `devices` (`cluster_id`);--> statement-breakpoint
CREATE TABLE `evidence_bundles` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text,
	`operation_id` text,
	`evidence_plane` text NOT NULL,
	`claim_type` text NOT NULL,
	`observed_at` text NOT NULL,
	`manifest_hash` text NOT NULL,
	`object_prefix` text NOT NULL,
	`retention_class` text NOT NULL,
	`redaction_class` text NOT NULL,
	`file_count` integer NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`operation_id`) REFERENCES `operations`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_evidence_manifest_hash` ON `evidence_bundles` (`manifest_hash`);--> statement-breakpoint
CREATE INDEX `idx_evidence_device_time` ON `evidence_bundles` (`device_id`,`observed_at`);--> statement-breakpoint
CREATE TABLE `federation_lanes` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text NOT NULL,
	`plane` text NOT NULL,
	`peer_fingerprint` text,
	`lane_type` text NOT NULL,
	`status` text NOT NULL,
	`trust_revision` text,
	`observed_at` text NOT NULL,
	`freshness_expires_at` text,
	`configuration_hash` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `idx_federation_device_plane` ON `federation_lanes` (`device_id`,`plane`);--> statement-breakpoint
CREATE TABLE `observed_states` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text NOT NULL,
	`observed_at` text NOT NULL,
	`ingested_at` text NOT NULL,
	`sequence` integer NOT NULL,
	`clock_confidence` text NOT NULL,
	`source_fingerprint` text NOT NULL,
	`state_json` text NOT NULL,
	`state_hash` text NOT NULL,
	`signature` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_observed_device_sequence` ON `observed_states` (`device_id`,`sequence`);--> statement-breakpoint
CREATE INDEX `idx_observed_device_time` ON `observed_states` (`device_id`,`observed_at`);--> statement-breakpoint
CREATE TABLE `operations` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text NOT NULL,
	`type` text NOT NULL,
	`status` text NOT NULL,
	`requested_by` text NOT NULL,
	`approved_by` text,
	`expected_state_hash` text,
	`desired_revision` text,
	`expires_at` text,
	`correlation_id` text NOT NULL,
	`device_receipt_hash` text,
	`failure_reason` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_operations_correlation` ON `operations` (`correlation_id`);--> statement-breakpoint
CREATE INDEX `idx_operations_device_status` ON `operations` (`device_id`,`status`);--> statement-breakpoint
CREATE TABLE `operator_roles` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`authenticated_user_id` text NOT NULL,
	`role` text NOT NULL,
	`scope_type` text NOT NULL,
	`scope_id` text NOT NULL,
	`expires_at` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_operator_roles_assignment` ON `operator_roles` (`organization_id`,`authenticated_user_id`,`role`,`scope_type`,`scope_id`);--> statement-breakpoint
CREATE TABLE `organizations` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `public_proposal_receipts` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text NOT NULL,
	`proposal_id` text NOT NULL,
	`state` text NOT NULL,
	`provenance_class` text NOT NULL,
	`location_precision_class` text NOT NULL,
	`observed_at` text NOT NULL,
	`expires_at` text NOT NULL,
	`confidence_basis` text NOT NULL,
	`content_hash` text NOT NULL,
	`receipt_hash` text,
	`committed_reference` text,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_public_proposal_device_id` ON `public_proposal_receipts` (`device_id`,`proposal_id`);--> statement-breakpoint
CREATE INDEX `idx_public_proposal_state_expiry` ON `public_proposal_receipts` (`state`,`expires_at`);--> statement-breakpoint
CREATE TABLE `public_query_sync` (
	`id` text PRIMARY KEY NOT NULL,
	`device_id` text NOT NULL,
	`scope_hash` text NOT NULL,
	`locality_label` text NOT NULL,
	`topic_scope` text NOT NULL,
	`transport` text NOT NULL,
	`last_successful_sync_at` text,
	`valid_until` text,
	`state` text NOT NULL,
	`updated_at` text NOT NULL,
	FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uidx_public_query_device_scope` ON `public_query_sync` (`device_id`,`scope_hash`);--> statement-breakpoint
CREATE INDEX `idx_public_query_state_valid` ON `public_query_sync` (`state`,`valid_until`);--> statement-breakpoint
CREATE TABLE `radio_observations` (
	`id` text PRIMARY KEY NOT NULL,
	`observer_device_id` text NOT NULL,
	`radio_fingerprint` text NOT NULL,
	`label` text,
	`observed_at` text NOT NULL,
	`signal_dbm` integer,
	`signal_noise_ratio` integer,
	`hops` integer,
	`region_code` text,
	`route_type` text NOT NULL,
	`trusted` integer NOT NULL,
	`observation_signature` text NOT NULL,
	FOREIGN KEY (`observer_device_id`) REFERENCES `devices`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `idx_radio_observer_time` ON `radio_observations` (`observer_device_id`,`observed_at`);--> statement-breakpoint
CREATE INDEX `idx_radio_fingerprint_time` ON `radio_observations` (`radio_fingerprint`,`observed_at`);
--> statement-breakpoint
PRAGMA optimize;
