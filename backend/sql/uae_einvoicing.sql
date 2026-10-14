/*
  UAE e-Invoicing extension for the existing SILA Fatoora SQL Server database.

  Every row is tenant scoped. Legal-entity records also carry LegalEntityId.
  This schema is additive. It does not alter Newyork_Person, access tokens,
  or the existing SAP S/4 and Ariba tables.

  Phase 1 persists through the application repository. When DB_NAME is
  configured, the same table shapes are the system of record.
*/

IF OBJECT_ID('dbo.Uae_Tenant', 'U') IS NULL
CREATE TABLE dbo.Uae_Tenant (
    TenantId        NVARCHAR(64)  NOT NULL PRIMARY KEY,
    Name            NVARCHAR(200) NOT NULL,
    Code            NVARCHAR(40)  NOT NULL,
    Status          NVARCHAR(20)  NOT NULL,
    DateCreated     DATETIME2     NOT NULL,
    DateUpdated     DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_LegalEntity', 'U') IS NULL
CREATE TABLE dbo.Uae_LegalEntity (
    LegalEntityId           NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId                NVARCHAR(64)  NOT NULL,
    EntityName              NVARCHAR(200) NOT NULL,
    LegalName               NVARCHAR(200) NOT NULL,
    EntityCode              NVARCHAR(40)  NOT NULL,
    Trn                     NVARCHAR(30)  NULL,
    Tin                     NVARCHAR(30)  NULL,
    PeppolIdentifier        NVARCHAR(80)  NULL,
    ElectronicAddress       NVARCHAR(120) NULL,
    ElectronicAddressScheme NVARCHAR(20)  NULL,
    TradeLicenceNumber      NVARCHAR(60)  NULL,
    TradeLicenceAuthority   NVARCHAR(120) NULL,
    VatRegistrationStatus   NVARCHAR(40)  NULL,
    VatGroup                NVARCHAR(80)  NULL,
    VatGroupMember          BIT           NOT NULL DEFAULT 0,
    AddressLine             NVARCHAR(240) NULL,
    Building                NVARCHAR(80)  NULL,
    Street                  NVARCHAR(160) NULL,
    City                    NVARCHAR(80)  NULL,
    Emirate                 NVARCHAR(40)  NULL,
    Country                 NVARCHAR(2)   NOT NULL DEFAULT 'AE',
    PostalCode              NVARCHAR(20)  NULL,
    DefaultCurrency         NVARCHAR(3)   NOT NULL DEFAULT 'AED',
    SourceSystems           NVARCHAR(400) NULL,
    AspId                   NVARCHAR(64)  NULL,
    EffectiveDate           DATE          NULL,
    Status                  NVARCHAR(20)  NOT NULL,
    DateCreated             DATETIME2     NOT NULL,
    DateUpdated             DATETIME2     NOT NULL
);
CREATE INDEX IX_Uae_LegalEntity_Tenant ON dbo.Uae_LegalEntity (TenantId);

IF OBJECT_ID('dbo.Uae_Connector', 'U') IS NULL
CREATE TABLE dbo.Uae_Connector (
    ConnectorId         NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId            NVARCHAR(64)  NOT NULL,
    ConnectorName       NVARCHAR(160) NOT NULL,
    SystemType          NVARCHAR(60)  NOT NULL,
    Direction           NVARCHAR(40)  NOT NULL,
    BaseUrl             NVARCHAR(400) NULL,
    AuthenticationType  NVARCHAR(40)  NOT NULL,
    CredentialReference NVARCHAR(200) NULL,
    HeadersJson         NVARCHAR(MAX) NULL,
    TimeoutSeconds      INT           NOT NULL,
    RetryPolicy         NVARCHAR(200) NULL,
    DeliveryMode        NVARCHAR(20)  NOT NULL,
    Frequency           NVARCHAR(80)  NULL,
    LegalEntityId       NVARCHAR(64)  NULL,
    Active              BIT           NOT NULL,
    LastConnection      DATETIME2     NULL,
    LastSuccess         DATETIME2     NULL,
    ConnectionStatus    NVARCHAR(40)  NOT NULL,
    DateCreated         DATETIME2     NOT NULL,
    DateUpdated         DATETIME2     NOT NULL
);
CREATE INDEX IX_Uae_Connector_Tenant ON dbo.Uae_Connector (TenantId);

IF OBJECT_ID('dbo.Uae_ConnectorLog', 'U') IS NULL
CREATE TABLE dbo.Uae_ConnectorLog (
    LogId        NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId     NVARCHAR(64)  NOT NULL,
    ConnectorId  NVARCHAR(64)  NOT NULL,
    At           DATETIME2     NOT NULL,
    Level        NVARCHAR(20)  NOT NULL,
    Message      NVARCHAR(1000) NOT NULL
);

IF OBJECT_ID('dbo.Uae_AspConfiguration', 'U') IS NULL
CREATE TABLE dbo.Uae_AspConfiguration (
    AspId                   NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId                NVARCHAR(64)  NOT NULL,
    AspName                 NVARCHAR(160) NOT NULL,
    ProviderType            NVARCHAR(80)  NOT NULL,
    EnvironmentName         NVARCHAR(40)  NOT NULL,
    ApiBaseUrl              NVARCHAR(400) NULL,
    AuthenticationType      NVARCHAR(40)  NOT NULL,
    CredentialReference     NVARCHAR(200) NULL,
    ParticipantId           NVARCHAR(120) NULL,
    PeppolScheme            NVARCHAR(20)  NULL,
    OutboundEndpoint        NVARCHAR(400) NULL,
    InboundEndpoint         NVARCHAR(400) NULL,
    StatusEndpoint          NVARCHAR(400) NULL,
    WebhookEndpoint         NVARCHAR(400) NULL,
    RetryConfiguration      NVARCHAR(200) NULL,
    TimeoutSeconds          INT           NOT NULL,
    Active                  BIT           NOT NULL,
    OperatingMode           NVARCHAR(20)  NOT NULL,
    ConnectionStatus        NVARCHAR(40)  NOT NULL,
    LastCommunication       DATETIME2     NULL,
    DateCreated             DATETIME2     NOT NULL,
    DateUpdated             DATETIME2     NOT NULL
);
CREATE INDEX IX_Uae_Asp_Tenant ON dbo.Uae_AspConfiguration (TenantId);

IF OBJECT_ID('dbo.Uae_MappingVersion', 'U') IS NULL
CREATE TABLE dbo.Uae_MappingVersion (
    MappingId       NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId        NVARCHAR(64)  NOT NULL,
    LegalEntityId   NVARCHAR(64)  NOT NULL,
    SourceSystem    NVARCHAR(60)  NOT NULL,
    DocumentType    NVARCHAR(60)  NOT NULL,
    Version         INT           NOT NULL,
    Status          NVARCHAR(20)  NOT NULL,
    EffectiveFrom   DATE          NULL,
    EffectiveTo     DATE          NULL,
    LinesJson       NVARCHAR(MAX) NOT NULL,
    DateCreated     DATETIME2     NOT NULL,
    DateUpdated     DATETIME2     NOT NULL
);
CREATE INDEX IX_Uae_Mapping_Scope ON dbo.Uae_MappingVersion (TenantId, LegalEntityId, SourceSystem, DocumentType);

IF OBJECT_ID('dbo.Uae_ComplianceRule', 'U') IS NULL
CREATE TABLE dbo.Uae_ComplianceRule (
    RuleRowId           NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId            NVARCHAR(64)  NOT NULL,
    RuleId              NVARCHAR(40)  NOT NULL,
    RuleName            NVARCHAR(200) NOT NULL,
    Description         NVARCHAR(800) NOT NULL,
    Category            NVARCHAR(60)  NOT NULL,
    BusinessTerm        NVARCHAR(120) NOT NULL,
    Severity            NVARCHAR(20)  NOT NULL,
    DocumentType        NVARCHAR(60)  NOT NULL,
    ConditionText       NVARCHAR(400) NULL,
    ValidationLogicJson NVARCHAR(MAX) NOT NULL,
    ErrorMessage        NVARCHAR(400) NOT NULL,
    SuggestedResolution NVARCHAR(400) NOT NULL,
    EffectiveFrom       DATE          NULL,
    EffectiveTo         DATE          NULL,
    Version             INT           NOT NULL,
    Active              BIT           NOT NULL,
    DateCreated         DATETIME2     NOT NULL,
    DateUpdated         DATETIME2     NOT NULL
);
CREATE INDEX IX_Uae_Rule_Tenant ON dbo.Uae_ComplianceRule (TenantId, RuleId, Version);

IF OBJECT_ID('dbo.Uae_Transaction', 'U') IS NULL
CREATE TABLE dbo.Uae_Transaction (
    TransactionId     NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId          NVARCHAR(64)  NOT NULL,
    LegalEntityId     NVARCHAR(64)  NOT NULL,
    Direction         NVARCHAR(40)  NOT NULL,
    DocumentType      NVARCHAR(60)  NOT NULL,
    InvoiceNumber     NVARCHAR(80)  NOT NULL,
    SourceSystem      NVARCHAR(60)  NOT NULL,
    SupplierName      NVARCHAR(200) NULL,
    BuyerName         NVARCHAR(200) NULL,
    InvoiceDate       DATE          NULL,
    Amount            DECIMAL(18,2) NOT NULL,
    CurrencyCode      NVARCHAR(3)   NOT NULL,
    SourceStatus      NVARCHAR(40)  NOT NULL,
    ComplianceStatus  NVARCHAR(40)  NOT NULL,
    AspStatus         NVARCHAR(40)  NOT NULL,
    NetworkStatus     NVARCHAR(40)  NOT NULL,
    ErpStatus         NVARCHAR(40)  NOT NULL,
    LifecycleStatus   NVARCHAR(40)  NOT NULL,
    CanonicalJson     NVARCHAR(MAX) NOT NULL,
    TimelineJson      NVARCHAR(MAX) NOT NULL,
    ClassificationJson NVARCHAR(MAX) NULL,
    MappingVersionId  NVARCHAR(64)  NULL,
    RuleVersion       INT           NULL,
    DateCreated       DATETIME2     NOT NULL,
    DateUpdated       DATETIME2     NOT NULL
);
CREATE INDEX IX_Uae_Transaction_Tenant ON dbo.Uae_Transaction (TenantId, LegalEntityId, LifecycleStatus);

IF OBJECT_ID('dbo.Uae_ValidationResult', 'U') IS NULL
CREATE TABLE dbo.Uae_ValidationResult (
    ResultId        NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId        NVARCHAR(64)  NOT NULL,
    LegalEntityId   NVARCHAR(64)  NOT NULL,
    TransactionId   NVARCHAR(64)  NOT NULL,
    RuleId          NVARCHAR(40)  NOT NULL,
    FieldPath       NVARCHAR(120) NULL,
    Severity        NVARCHAR(20)  NOT NULL,
    Status          NVARCHAR(20)  NOT NULL,
    SourceValue     NVARCHAR(400) NULL,
    MappedValue     NVARCHAR(400) NULL,
    Expected        NVARCHAR(400) NULL,
    Message         NVARCHAR(400) NULL,
    Resolution      NVARCHAR(400) NULL,
    DateCreated     DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_Exception', 'U') IS NULL
CREATE TABLE dbo.Uae_Exception (
    ExceptionId     NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId        NVARCHAR(64)  NOT NULL,
    LegalEntityId   NVARCHAR(64)  NOT NULL,
    TransactionId   NVARCHAR(64)  NOT NULL,
    Severity        NVARCHAR(20)  NOT NULL,
    Category        NVARCHAR(40)  NOT NULL,
    Owner           NVARCHAR(120) NULL,
    AgeHours        INT           NOT NULL,
    Status          NVARCHAR(20)  NOT NULL,
    Resolution      NVARCHAR(400) NULL,
    DateCreated     DATETIME2     NOT NULL,
    DateUpdated     DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_Reconciliation', 'U') IS NULL
CREATE TABLE dbo.Uae_Reconciliation (
    ItemId          NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId        NVARCHAR(64)  NOT NULL,
    LegalEntityId   NVARCHAR(64)  NULL,
    TransactionId   NVARCHAR(64)  NULL,
    SourceSystem    NVARCHAR(60)  NULL,
    DifferenceType  NVARCHAR(60)  NOT NULL,
    SourceAmount    DECIMAL(18,2) NULL,
    SilaAmount      DECIMAL(18,2) NULL,
    AspAmount       DECIMAL(18,2) NULL,
    NetworkState    NVARCHAR(40)  NULL,
    AribaState      NVARCHAR(40)  NULL,
    S4State         NVARCHAR(40)  NULL,
    Detail          NVARCHAR(400) NULL,
    DateCreated     DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_ArchiveArtifact', 'U') IS NULL
CREATE TABLE dbo.Uae_ArchiveArtifact (
    ArtifactId        NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId          NVARCHAR(64)  NOT NULL,
    LegalEntityId     NVARCHAR(64)  NOT NULL,
    TransactionId     NVARCHAR(64)  NOT NULL,
    Kind              NVARCHAR(60)  NOT NULL,
    MappingVersionId  NVARCHAR(64)  NULL,
    RuleVersion       INT           NULL,
    PayloadJson       NVARCHAR(MAX) NULL,
    DateCreated       DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_RetentionPolicy', 'U') IS NULL
CREATE TABLE dbo.Uae_RetentionPolicy (
    PolicyId        NVARCHAR(64) NOT NULL PRIMARY KEY,
    TenantId        NVARCHAR(64) NOT NULL,
    LegalEntityId   NVARCHAR(64) NOT NULL,
    RetentionYears  INT          NOT NULL,
    DateUpdated     DATETIME2    NOT NULL
);

IF OBJECT_ID('dbo.Uae_AuditEvent', 'U') IS NULL
CREATE TABLE dbo.Uae_AuditEvent (
    AuditId           NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId          NVARCHAR(64)  NOT NULL,
    Actor             NVARCHAR(120) NOT NULL,
    At                DATETIME2     NOT NULL,
    Action            NVARCHAR(80)  NOT NULL,
    OldValue          NVARCHAR(MAX) NULL,
    NewValue          NVARCHAR(MAX) NULL,
    TransactionId     NVARCHAR(64)  NULL,
    ConfigurationArea NVARCHAR(60)  NOT NULL,
    Reason            NVARCHAR(400) NULL,
    IpAddress         NVARCHAR(64)  NULL,
    SessionId         NVARCHAR(80)  NULL
);
CREATE INDEX IX_Uae_Audit_Tenant ON dbo.Uae_AuditEvent (TenantId, At);

IF OBJECT_ID('dbo.Uae_RoleAssignment', 'U') IS NULL
CREATE TABLE dbo.Uae_RoleAssignment (
    AssignmentId    NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId        NVARCHAR(64)  NOT NULL,
    PersonName      NVARCHAR(160) NOT NULL,
    SilaRole        NVARCHAR(40)  NOT NULL,
    UaeRole         NVARCHAR(80)  NOT NULL,
    DateUpdated     DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_MasterData', 'U') IS NULL
CREATE TABLE dbo.Uae_MasterData (
    TenantId     NVARCHAR(64) NOT NULL PRIMARY KEY,
    PayloadJson  NVARCHAR(MAX) NOT NULL,
    DateUpdated  DATETIME2    NOT NULL
);

IF OBJECT_ID('dbo.Uae_ComplianceProfile', 'U') IS NULL
CREATE TABLE dbo.Uae_ComplianceProfile (
    ProfileId            NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId             NVARCHAR(64)  NOT NULL,
    Country              NVARCHAR(8)   NOT NULL,
    ProfileName          NVARCHAR(80)  NOT NULL,
    StandardName         NVARCHAR(40)  NOT NULL,
    VersionLabel         NVARCHAR(40)  NOT NULL,
    Status               NVARCHAR(20)  NOT NULL,
    EffectiveFrom        DATE          NOT NULL,
    EffectiveTo          DATE          NULL,
    RegulatorySource     NVARCHAR(240) NULL,
    SourceDocument       NVARCHAR(240) NULL,
    BusinessProcessId    NVARCHAR(180) NULL,
    SpecificationId      NVARCHAR(180) NULL,
    PayloadJson          NVARCHAR(MAX) NOT NULL,
    DateUpdated          DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_CodeList', 'U') IS NULL
CREATE TABLE dbo.Uae_CodeList (
    CodeListId     NVARCHAR(64)  NOT NULL PRIMARY KEY,
    ListName       NVARCHAR(80)  NOT NULL,
    Code           NVARCHAR(40)  NOT NULL,
    Description    NVARCHAR(240) NOT NULL,
    SourceName     NVARCHAR(160) NULL,
    VersionLabel   NVARCHAR(40)  NULL,
    EffectiveDate  DATE          NULL,
    Active         BIT           NOT NULL,
    PayloadJson    NVARCHAR(MAX) NULL
);

IF OBJECT_ID('dbo.Uae_FieldRegistry', 'U') IS NULL
CREATE TABLE dbo.Uae_FieldRegistry (
    FieldId          NVARCHAR(120) NOT NULL PRIMARY KEY,
    BusinessTermId   NVARCHAR(40)  NULL,
    DisplayName      NVARCHAR(160) NOT NULL,
    SectionName      NVARCHAR(40)  NOT NULL,
    DataType         NVARCHAR(40)  NOT NULL,
    RequirementLevel NVARCHAR(20)  NOT NULL,
    PayloadJson      NVARCHAR(MAX) NOT NULL
);

IF OBJECT_ID('dbo.Uae_ValidationStage', 'U') IS NULL
CREATE TABLE dbo.Uae_ValidationStage (
    StageId        NVARCHAR(64) NOT NULL PRIMARY KEY,
    TransactionId  NVARCHAR(64) NOT NULL,
    TenantId       NVARCHAR(64) NOT NULL,
    StageName      NVARCHAR(80) NOT NULL,
    StageResult    NVARCHAR(20) NOT NULL,
    DateCreated    DATETIME2    NOT NULL
);

IF OBJECT_ID('dbo.Uae_Correction', 'U') IS NULL
CREATE TABLE dbo.Uae_Correction (
    CorrectionId   NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TransactionId  NVARCHAR(64)  NOT NULL,
    TenantId       NVARCHAR(64)  NOT NULL,
    FieldPath      NVARCHAR(180) NOT NULL,
    ProposedValue  NVARCHAR(MAX) NULL,
    Reason         NVARCHAR(400) NOT NULL,
    Actor          NVARCHAR(120) NOT NULL,
    DateCreated    DATETIME2     NOT NULL
);

IF OBJECT_ID('dbo.Uae_Property', 'U') IS NULL
CREATE TABLE dbo.Uae_Property (
    PropertyId       NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId         NVARCHAR(64)  NOT NULL,
    PropertyCode     NVARCHAR(40)  NOT NULL,
    PropertyName     NVARCHAR(160) NULL,
    LegalEntityId    NVARCHAR(64)  NULL,
    OperaHotelCode   NVARCHAR(40)  NULL,
    Status           NVARCHAR(40)  NOT NULL,
    PayloadJson      NVARCHAR(MAX) NULL
);

IF OBJECT_ID('dbo.Uae_EntityResolution', 'U') IS NULL
CREATE TABLE dbo.Uae_EntityResolution (
    ResolutionId   NVARCHAR(64) NOT NULL PRIMARY KEY,
    TenantId       NVARCHAR(64) NOT NULL,
    SourceSystem   NVARCHAR(40) NOT NULL,
    KeyType        NVARCHAR(60) NOT NULL,
    KeyValue       NVARCHAR(80) NOT NULL,
    LegalEntityId  NVARCHAR(64) NOT NULL,
    Active         BIT          NOT NULL
);

IF OBJECT_ID('dbo.Uae_DocumentRoute', 'U') IS NULL
CREATE TABLE dbo.Uae_DocumentRoute (
    RouteId        NVARCHAR(64) NOT NULL PRIMARY KEY,
    TenantId       NVARCHAR(64) NOT NULL,
    SourceSystem   NVARCHAR(40) NOT NULL,
    DocumentClass  NVARCHAR(80) NOT NULL,
    RouteName      NVARCHAR(80) NOT NULL,
    InScope        BIT          NOT NULL,
    PayloadJson    NVARCHAR(MAX) NULL
);

IF OBJECT_ID('dbo.Uae_BuyerProfile', 'U') IS NULL
CREATE TABLE dbo.Uae_BuyerProfile (
    BuyerId          NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId         NVARCHAR(64)  NOT NULL,
    LegalName        NVARCHAR(200) NULL,
    Trn              NVARCHAR(20)  NULL,
    OperaCustomerCode NVARCHAR(40) NULL,
    SapCustomerNumber NVARCHAR(40) NULL,
    AribaVendor      NVARCHAR(40)  NULL,
    Status           NVARCHAR(20)  NOT NULL,
    PayloadJson      NVARCHAR(MAX) NULL
);

IF OBJECT_ID('dbo.Uae_SourcePayload', 'U') IS NULL
CREATE TABLE dbo.Uae_SourcePayload (
    PayloadId      NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TenantId       NVARCHAR(64)  NOT NULL,
    TransactionId  NVARCHAR(64)  NULL,
    SourceSystem   NVARCHAR(40)  NOT NULL,
    PayloadHash    NVARCHAR(64)  NOT NULL,
    ReceivedAt     DATETIME2     NOT NULL,
    PayloadJson    NVARCHAR(MAX) NOT NULL
);

