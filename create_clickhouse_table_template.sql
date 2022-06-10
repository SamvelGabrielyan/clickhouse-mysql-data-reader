CREATE DATABASE IF NOT EXISTS `risk_management`;
CREATE TABLE IF NOT EXISTS `risk_management`.`tickets_ticket`  (
    `id` Int64,
    `platform` String,
    `description` String,
    `close_description` String,
    `case_id` Int64,
    `status_id` Int64,
    `ticket_type_id` Int64,
    `message` Int8,
    `dangerous` Int8,
    `amount` Int32,
    `factor` String,
    `session_id` String,
    `win` Int32,
    `currency` String,
    `close_date` DateTime,
    `bet_id` String,
    `game_id` String,
    `manager_close_id` Int32,
    `session_token` String,
    `external_id` String,
    `parameter` String
) 
ENGINE = MergeTree() PARTITION BY id ORDER BY (id)
;

create database ch_test;
CREATE TABLE IF NOT EXISTS `ch_test`.`Person`  (
    PersonID Int64,
    LastName String,
    FirstName String,
    Address String,
    City String
)
ENGINE = MergeTree() PARTITION BY PersonID ORDER BY (PersonID)
;


CREATE PROCEDURE myproc()
BEGIN
    DECLARE i int DEFAULT 27692001;
    WHILE i <= 237692004 DO
        INSERT INTO mytable (code, active, total) VALUES (i, 1, 1);
        SET i = i + 1;
    END WHILE;
END