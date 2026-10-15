from typing import Iterable, Sequence


SCHEMA_STATEMENTS: Sequence[str] = (
    """
    IF OBJECT_ID('dbo.ref_status', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_status (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_document_types', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_document_types (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_signing_types', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_signing_types (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_document_kinds', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_document_kinds (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_published_where', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_published_where (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_executors', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_executors (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE,
            position NVARCHAR(255) NULL,
            department NVARCHAR(255) NULL,
            is_active BIT NOT NULL CONSTRAINT DF_ref_executors_is_active DEFAULT 1,
            created_at DATETIME2 NOT NULL CONSTRAINT DF_ref_executors_created_at DEFAULT SYSUTCDATETIME()
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_responsible_executors', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_responsible_executors (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE,
            is_active BIT NOT NULL CONSTRAINT DF_ref_responsible_executors_is_active DEFAULT 1
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_themes', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_themes (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE,
            description NVARCHAR(MAX) NULL,
            is_active BIT NOT NULL CONSTRAINT DF_ref_themes_is_active DEFAULT 1,
            created_at DATETIME2 NOT NULL CONSTRAINT DF_ref_themes_created_at DEFAULT SYSUTCDATETIME()
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_signers', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_signers (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.ref_approvers', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.ref_approvers (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255) NOT NULL UNIQUE
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.documents', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.documents (
            id INT IDENTITY(1,1) PRIMARY KEY,
            reg_number NVARCHAR(255) NULL,
            reg_date NVARCHAR(32) NULL,
            number NVARCHAR(255) NULL,
            status_id INT NULL,
            type_id INT NULL,
            signing_type_id INT NULL,
            document_kind_id INT NULL,
            theme_id INT NULL,
            executor_id INT NULL,
            responsible_executor_id INT NULL,
            title NVARCHAR(MAX) NULL,
            document_path NVARCHAR(1024) NULL,
            should_publish NVARCHAR(255) NULL,
            published_where_id INT NULL,
            published_date NVARCHAR(32) NULL,
            control_date NVARCHAR(32) NULL,
            removed_from_control NVARCHAR(255) NULL,
            execution_result NVARCHAR(MAX) NULL,
            pages_count INT NULL,
            attachments_count INT NULL,
            case_number NVARCHAR(255) NULL,
            volume_number NVARCHAR(255) NULL,
            sheets NVARCHAR(255) NULL,
            created_at DATETIME2 NOT NULL CONSTRAINT DF_documents_created_at DEFAULT SYSUTCDATETIME(),
            row_version ROWVERSION NOT NULL
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.document_signers', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.document_signers (
            id INT IDENTITY(1,1) PRIMARY KEY,
            document_id INT NOT NULL,
            signer_id INT NOT NULL,
            CONSTRAINT UQ_document_signers UNIQUE (document_id, signer_id)
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.document_approvers', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.document_approvers (
            id INT IDENTITY(1,1) PRIMARY KEY,
            document_id INT NOT NULL,
            approver_id INT NOT NULL,
            CONSTRAINT UQ_document_approvers UNIQUE (document_id, approver_id)
        );
    END
    """,
    """
    IF OBJECT_ID('dbo.db_metadata', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.db_metadata (
            id INT NOT NULL PRIMARY KEY,
            last_backup_date DATETIME2 NULL,
            backup_count INT NOT NULL CONSTRAINT DF_db_metadata_backup_count DEFAULT 0,
            created_at DATETIME2 NOT NULL CONSTRAINT DF_db_metadata_created_at DEFAULT SYSUTCDATETIME(),
            updated_at DATETIME2 NOT NULL CONSTRAINT DF_db_metadata_updated_at DEFAULT SYSUTCDATETIME()
        );
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM dbo.db_metadata WHERE id = 1)
    BEGIN
        INSERT INTO dbo.db_metadata (id, backup_count) VALUES (1, 0);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_status')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_status
            FOREIGN KEY (status_id) REFERENCES dbo.ref_status(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_document_types')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_document_types
            FOREIGN KEY (type_id) REFERENCES dbo.ref_document_types(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_signing_types')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_signing_types
            FOREIGN KEY (signing_type_id) REFERENCES dbo.ref_signing_types(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_document_kinds')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_document_kinds
            FOREIGN KEY (document_kind_id) REFERENCES dbo.ref_document_kinds(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_themes')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_themes
            FOREIGN KEY (theme_id) REFERENCES dbo.ref_themes(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_executors')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_executors
            FOREIGN KEY (executor_id) REFERENCES dbo.ref_executors(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_responsible_executors')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_responsible_executors
            FOREIGN KEY (responsible_executor_id) REFERENCES dbo.ref_responsible_executors(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_documents_ref_published_where')
    BEGIN
        ALTER TABLE dbo.documents ADD CONSTRAINT FK_documents_ref_published_where
            FOREIGN KEY (published_where_id) REFERENCES dbo.ref_published_where(id);
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_document_signers_documents')
    BEGIN
        ALTER TABLE dbo.document_signers ADD CONSTRAINT FK_document_signers_documents
            FOREIGN KEY (document_id) REFERENCES dbo.documents(id) ON DELETE CASCADE;
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_document_signers_ref_signers')
    BEGIN
        ALTER TABLE dbo.document_signers ADD CONSTRAINT FK_document_signers_ref_signers
            FOREIGN KEY (signer_id) REFERENCES dbo.ref_signers(id) ON DELETE CASCADE;
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_document_approvers_documents')
    BEGIN
        ALTER TABLE dbo.document_approvers ADD CONSTRAINT FK_document_approvers_documents
            FOREIGN KEY (document_id) REFERENCES dbo.documents(id) ON DELETE CASCADE;
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_document_approvers_ref_approvers')
    BEGIN
        ALTER TABLE dbo.document_approvers ADD CONSTRAINT FK_document_approvers_ref_approvers
            FOREIGN KEY (approver_id) REFERENCES dbo.ref_approvers(id) ON DELETE CASCADE;
    END
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_docs_status' AND object_id = OBJECT_ID('dbo.documents'))
        CREATE INDEX idx_docs_status ON dbo.documents(status_id);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_docs_type' AND object_id = OBJECT_ID('dbo.documents'))
        CREATE INDEX idx_docs_type ON dbo.documents(type_id);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_docs_executor' AND object_id = OBJECT_ID('dbo.documents'))
        CREATE INDEX idx_docs_executor ON dbo.documents(executor_id);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_docs_theme' AND object_id = OBJECT_ID('dbo.documents'))
        CREATE INDEX idx_docs_theme ON dbo.documents(theme_id);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_docs_reg_date' AND object_id = OBJECT_ID('dbo.documents'))
        CREATE INDEX idx_docs_reg_date ON dbo.documents(reg_date);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_docs_reg_number' AND object_id = OBJECT_ID('dbo.documents'))
        CREATE INDEX idx_docs_reg_number ON dbo.documents(reg_number);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_docs_reg_date_id' AND object_id = OBJECT_ID('dbo.documents'))
        CREATE INDEX idx_docs_reg_date_id ON dbo.documents(reg_date DESC, id DESC);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_doc_signers_doc' AND object_id = OBJECT_ID('dbo.document_signers'))
        CREATE INDEX idx_doc_signers_doc ON dbo.document_signers(document_id);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_doc_signers_signer' AND object_id = OBJECT_ID('dbo.document_signers'))
        CREATE INDEX idx_doc_signers_signer ON dbo.document_signers(signer_id);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_doc_approvers_doc' AND object_id = OBJECT_ID('dbo.document_approvers'))
        CREATE INDEX idx_doc_approvers_doc ON dbo.document_approvers(document_id);
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_doc_approvers_approver' AND object_id = OBJECT_ID('dbo.document_approvers'))
        CREATE INDEX idx_doc_approvers_approver ON dbo.document_approvers(approver_id);
    """,
)


DEFAULT_REFERENCE_DATA = {
    "ref_status": [
        "Внесены дополнения",
        "Внесены дополнения и изменения",
        "Внесены изменения",
        "Действует",
        "Утратило силу",
        "Отменено",
    ],
    "ref_document_types": ["Постановление", "Распоряжение"],
    "ref_signing_types": ["Одностороннее", "Многостороннее"],
    "ref_document_kinds": ["Ненормативный правовой акт", "Нормативный правовой акт"],
    "ref_published_where": ['"Сосновская Нива"', 'Информационный бюллетень "Сосновская Нива"'],
}


def execute_schema_statements(cursor, statements: Iterable[str] = SCHEMA_STATEMENTS) -> None:
    for statement in statements:
        cursor.execute(statement)


def seed_reference_data(cursor, data: dict = DEFAULT_REFERENCE_DATA) -> None:
    for table_name, values in data.items():
        for value in values:
            cursor.execute(
                f"""
                IF NOT EXISTS (SELECT 1 FROM dbo.{table_name} WHERE name = ?)
                BEGIN
                    INSERT INTO dbo.{table_name} (name) VALUES (?);
                END
                """,
                (value, value),
            )
