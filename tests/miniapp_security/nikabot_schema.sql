-- Structure only (read from the Data dictionary page of the live nikabot database; no data, no customer rows).
-- Secondary indexes were left out on purpose except users.userid; this is a test database.
SET NAMES utf8mb4;

DROP TABLE IF EXISTS `add_link`;
CREATE TABLE `add_link` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `remark` varchar(100) NOT NULL,
  `token` varchar(255) NOT NULL,
  `added_by` bigint(20) NOT NULL,
  `created_at` datetime NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `admins`;
CREATE TABLE `admins` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `username` varchar(200) NOT NULL,
  `password` varchar(200) NOT NULL,
  `backupchannel` varchar(200) NOT NULL,
  `lang` varchar(10) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `bc_jobs`;
CREATE TABLE `bc_jobs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `broadcast_id` int(11) NOT NULL,
  `action` enum('pin','unpin','delete','edit') NOT NULL,
  `new_text` text NULL DEFAULT NULL,
  `parse_mode` varchar(20) NULL DEFAULT NULL,
  `offset` int(11) NOT NULL DEFAULT 0,
  `state` tinyint(4) NOT NULL DEFAULT 1,
  `ok_cnt` int(11) NOT NULL DEFAULT 0,
  `fail_cnt` int(11) NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `bc_messages`;
CREATE TABLE `bc_messages` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `broadcast_id` int(11) NOT NULL,
  `userid` bigint(20) NOT NULL,
  `message_id` int(11) NOT NULL,
  `pinned` tinyint(1) NOT NULL DEFAULT 0,
  `deleted` tinyint(1) NOT NULL DEFAULT 0,
  `sent_at` datetime NOT NULL DEFAULT current_timestamp(),
  `pinned_at` datetime NULL DEFAULT NULL,
  `deleted_at` datetime NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `black_list`;
CREATE TABLE `black_list` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `info` varchar(20) NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `chats`;
CREATE TABLE `chats` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` bigint(20) NOT NULL,
  `create_date` int(11) NOT NULL,
  `title` varchar(1000) NOT NULL,
  `category` varchar(1000) NOT NULL,
  `state` int(11) NOT NULL,
  `rate` int(11) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `chats_info`;
CREATE TABLE `chats_info` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `chat_id` int(11) NOT NULL,
  `sent_date` int(11) NOT NULL,
  `msg_type` varchar(50) NULL DEFAULT NULL,
  `text` varchar(2000) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `discounts`;
CREATE TABLE `discounts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `hash_id` varchar(100) NOT NULL,
  `type` varchar(10) NOT NULL,
  `amount` int(11) NOT NULL,
  `expire_date` int(11) NOT NULL,
  `expire_count` int(11) NOT NULL,
  `used_by` text NULL DEFAULT NULL,
  `can_use` int(11) NOT NULL DEFAULT 1,
  `audience_type` varchar(32) NOT NULL DEFAULT 'all',
  `audience_user_id` bigint(20) NULL DEFAULT NULL,
  `target_type` varchar(32) NOT NULL DEFAULT 'all',
  `target_id` int(11) NULL DEFAULT NULL,
  `min_price` int(11) NOT NULL DEFAULT 0,
  `max_discount` int(11) NULL DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `gift_list`;
CREATE TABLE `gift_list` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `server_id` int(11) NOT NULL,
  `volume` int(11) NOT NULL,
  `day` int(11) NOT NULL,
  `offset` int(11) NULL DEFAULT 0,
  `server_offset` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `increase_day`;
CREATE TABLE `increase_day` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `volume` float NOT NULL,
  `price` int(11) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `increase_order`;
CREATE TABLE `increase_order` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `userid` varchar(30) NOT NULL,
  `server_id` int(11) NOT NULL,
  `inbound_id` int(11) NOT NULL,
  `remark` varchar(100) NOT NULL,
  `amount` int(11) NOT NULL,
  `date` varchar(30) NOT NULL,
  `volume` int(11) NULL DEFAULT NULL,
  `day` int(11) NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `increase_plan`;
CREATE TABLE `increase_plan` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `volume` float NOT NULL,
  `price` int(11) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `needed_sofwares`;
CREATE TABLE `needed_sofwares` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `title` varchar(100) NOT NULL,
  `link` varchar(250) NOT NULL,
  `status` int(11) NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `orders_list`;
CREATE TABLE `orders_list` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `userid` varchar(30) NOT NULL,
  `token` varchar(1000) NOT NULL,
  `transid` varchar(150) NOT NULL,
  `fileid` int(11) NOT NULL,
  `server_id` int(11) NOT NULL,
  `inbound_id` int(11) NOT NULL DEFAULT 0,
  `remark` varchar(100) NOT NULL,
  `uuid` text NOT NULL,
  `protocol` varchar(20) NOT NULL,
  `expire_date` int(11) NOT NULL,
  `link` text NOT NULL,
  `amount` int(11) NOT NULL,
  `status` int(11) NOT NULL,
  `date` varchar(50) NOT NULL,
  `notif` int(11) NOT NULL DEFAULT 0,
  `rahgozar` int(11) NULL DEFAULT 0,
  `agent_bought` int(11) NOT NULL DEFAULT 0,
  `autoRenew` varchar(10) NULL DEFAULT 'off',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `pays`;
CREATE TABLE `pays` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `hash_id` varchar(1000) NOT NULL,
  `description` varchar(5000) NULL DEFAULT NULL,
  `payid` varchar(500) NULL DEFAULT NULL,
  `user_id` bigint(20) NOT NULL,
  `type` varchar(100) NULL DEFAULT NULL,
  `plan_id` int(11) NULL DEFAULT NULL,
  `server_id` int(11) NULL DEFAULT NULL,
  `volume` int(11) NULL DEFAULT NULL,
  `day` int(11) NULL DEFAULT NULL,
  `price` int(11) NOT NULL,
  `request_date` int(11) NOT NULL,
  `state` varchar(255) NOT NULL,
  `agent_bought` int(11) NOT NULL DEFAULT 0,
  `agent_count` int(11) NOT NULL DEFAULT 0,
  `message_id` int(11) NULL DEFAULT NULL,
  `chat_id` varchar(500) NULL DEFAULT NULL,
  `order_id` int(11) NULL DEFAULT NULL,
  `admins_messages` text NULL DEFAULT NULL,
  `approved_by` bigint(20) NULL DEFAULT NULL,
  `updated_at` datetime NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `send_list`;
CREATE TABLE `send_list` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `offset` int(11) NOT NULL DEFAULT 0,
  `type` varchar(20) NOT NULL,
  `title` varchar(255) NULL DEFAULT NULL,
  `text` text NULL DEFAULT NULL,
  `chat_id` bigint(20) NULL DEFAULT NULL,
  `message_id` int(11) NULL DEFAULT NULL,
  `file_id` varchar(500) NULL DEFAULT NULL,
  `state` int(11) NOT NULL DEFAULT 0,
  `target_group` varchar(50) NOT NULL DEFAULT 'all',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NULL DEFAULT NULL,
  `sample_userid` bigint(20) NULL DEFAULT NULL,
  `sample_message_id` int(11) NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `servers`;
CREATE TABLE `servers` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `ip` varchar(200) NOT NULL,
  `port` int(11) NOT NULL,
  `username` varchar(200) NOT NULL,
  `password` varchar(200) NOT NULL,
  `name` varchar(200) NOT NULL,
  `panel` varchar(100) NOT NULL,
  `status` int(11) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `server_categories`;
CREATE TABLE `server_categories` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `server_id` varchar(20) NOT NULL,
  `title` varchar(50) NOT NULL,
  `parent` int(11) NOT NULL DEFAULT 0,
  `step` int(11) NOT NULL,
  `active` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `server_config`;
CREATE TABLE `server_config` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `panel_url` varchar(254) NOT NULL,
  `ip` mediumtext NOT NULL,
  `sni` varchar(254) NOT NULL,
  `header_type` enum('none','http') NOT NULL,
  `request_header` mediumtext NOT NULL,
  `response_header` mediumtext NOT NULL,
  `security` enum('xtls','tls','none') NOT NULL,
  `tlsSettings` mediumtext NOT NULL,
  `type` varchar(100) NULL DEFAULT NULL,
  `username` varchar(100) NOT NULL,
  `password` varchar(100) NOT NULL,
  `port_type` varchar(10) NULL DEFAULT 'auto',
  `reality` varchar(10) NULL DEFAULT 'false',
  `sub_links` mediumtext NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `server_info`;
CREATE TABLE `server_info` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `title` varchar(200) NOT NULL,
  `ucount` varchar(20) NOT NULL,
  `remark` varchar(100) NOT NULL,
  `flag` varchar(100) NOT NULL,
  `active` int(11) NOT NULL DEFAULT 0,
  `state` int(11) NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `server_plans`;
CREATE TABLE `server_plans` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `fileid` varchar(250) NOT NULL,
  `catid` int(11) NOT NULL,
  `server_id` int(11) NOT NULL,
  `inbound_id` int(11) NOT NULL DEFAULT 0,
  `acount` bigint(20) NOT NULL,
  `limitip` int(11) NOT NULL DEFAULT 1,
  `title` varchar(150) NOT NULL,
  `protocol` varchar(100) NOT NULL,
  `days` float NOT NULL,
  `volume` float NOT NULL,
  `type` varchar(50) NOT NULL,
  `price` int(11) NOT NULL,
  `descr` text NOT NULL,
  `pic` varchar(100) NOT NULL,
  `active` int(11) NOT NULL DEFAULT 0,
  `step` int(11) NOT NULL,
  `date` varchar(50) NOT NULL,
  `rahgozar` int(11) NULL DEFAULT 0,
  `dest` varchar(1000) NULL DEFAULT NULL,
  `serverNames` text NULL DEFAULT NULL,
  `spiderX` varchar(500) NULL DEFAULT NULL,
  `flow` varchar(50) NULL DEFAULT NULL,
  `custom_path` int(11) NULL DEFAULT 1,
  `custom_port` int(11) NOT NULL DEFAULT 0,
  `custom_sni` longtext NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `setting`;
CREATE TABLE `setting` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `type` varchar(500) NOT NULL,
  `value` text NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `userid` varchar(40) NOT NULL,
  `name` varchar(100) NOT NULL,
  `username` varchar(100) NOT NULL,
  `refcode` varchar(50) NOT NULL,
  `wallet` int(11) NOT NULL DEFAULT 0,
  `date` varchar(50) NOT NULL,
  `phone` varchar(15) NULL DEFAULT NULL,
  `refered_by` bigint(20) NULL DEFAULT NULL,
  `step` varchar(1000) NOT NULL DEFAULT 'none',
  `freetrial` varchar(10) NULL DEFAULT NULL,
  `isAdmin` tinyint(1) NOT NULL DEFAULT 0,
  `first_start` varchar(10) NULL DEFAULT NULL,
  `temp` text NULL DEFAULT NULL,
  `is_agent` int(11) NOT NULL DEFAULT 0,
  `discount_percent` varchar(1000) NULL DEFAULT NULL,
  `agent_date` int(11) NOT NULL DEFAULT 0,
  `spam_info` varchar(500) NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `userid` (`userid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
