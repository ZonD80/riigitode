from django.db import models
from django.utils import timezone


class Politician(models.Model):
    """Model representing a politician/parliament member"""
    
    uuid = models.CharField(max_length=36, unique=True, db_index=True, 
                           help_text="UUID from Riigikogu API")
    first_name = models.CharField(max_length=100, help_text="Eesnimi")
    last_name = models.CharField(max_length=100, help_text="Perekonnanimi")
    full_name = models.CharField(max_length=200, help_text="Täisnimi")
    active = models.BooleanField(default=True, help_text="Aktiivne liige")
    email = models.EmailField(blank=True, null=True, help_text="E-mail")
    phone = models.CharField(max_length=50, blank=True, null=True, help_text="Telefon")
    gender = models.CharField(max_length=10, blank=True, null=True, help_text="Sugu")
    date_of_birth = models.DateField(blank=True, null=True, help_text="Sünni kuupäev")
    parliament_seniority = models.IntegerField(blank=True, null=True, 
                                             help_text="Staaž parlamendis")
    total_time_seconds = models.IntegerField(blank=True, null=True, help_text="Kogu kõneaeg sekundites")
    
    # Profiling count fields
    profiles_required = models.IntegerField(default=0, help_text="Profiilide arv, mida on vaja")
    profiles_already_profiled = models.IntegerField(default=0, help_text="Juba profileeritud profiilide arv")
    
    # Photo fields
    photo = models.ImageField(upload_to='politicians/photos/', blank=True, null=True, 
                             help_text="Poliitiku foto")
    photo_big = models.ImageField(upload_to='politicians/photos_big/', blank=True, null=True, 
                                 help_text="Poliitiku suur foto")
    photo_uuid = models.CharField(max_length=36, blank=True, null=True, 
                                 help_text="Foto UUID API-st")
    photo_filename = models.CharField(max_length=255, blank=True, null=True, 
                                     help_text="Foto failinimi API-st")
    photo_extension = models.CharField(max_length=10, blank=True, null=True, 
                                      help_text="Foto faililaiend API-st")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Politician"
        verbose_name_plural = "Politicians"
        ordering = ['last_name', 'first_name']
        
    def __str__(self):
        return self.full_name or f"{self.first_name} {self.last_name}"
    
    @property
    def formatted_total_time(self):
        """Return formatted total speaking time as hours:minutes"""
        if not self.total_time_seconds:
            return None
        
        hours = self.total_time_seconds // 3600
        minutes = (self.total_time_seconds % 3600) // 60
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    
    @property
    def current_faction(self):
        """Return the politician's current faction (active membership without end_date)"""
        current_membership = self.faction_memberships.filter(
            end_date__isnull=True
        ).select_related('faction').first()
        
        if current_membership:
            return current_membership.faction
        return None
    
    @property
    def profiling_percentage(self):
        """Return profiling completion percentage"""
        if not self.profiles_required or self.profiles_required == 0:
            return 0
        return round((self.profiles_already_profiled / self.profiles_required * 100), 1)


class Faction(models.Model):
    """Model representing a political faction"""
    
    uuid = models.CharField(max_length=36, unique=True, db_index=True,
                           help_text="UUID from Riigikogu API")
    name = models.CharField(max_length=200, help_text="Fraktsiooni nimi")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Faction"
        verbose_name_plural = "Factions"
        ordering = ['name']
        
    def __str__(self):
        return self.name


class PoliticianFaction(models.Model):
    """Model representing politician's membership in a faction"""
    
    politician = models.ForeignKey(Politician, on_delete=models.CASCADE,
                                 related_name='faction_memberships')
    faction = models.ForeignKey(Faction, on_delete=models.CASCADE,
                              related_name='members')
    start_date = models.DateField(blank=True, null=True, help_text="Liikmelisuse algus")
    end_date = models.DateField(blank=True, null=True, help_text="Liikmelisuse lõpp")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Politician Faction Membership"
        verbose_name_plural = "Politician Faction Memberships"
        unique_together = ['politician', 'faction', 'start_date']
        
    def __str__(self):
        return f"{self.politician} - {self.faction}"


class PlenarySession(models.Model):
    """Model representing a plenary session"""
    
    membership = models.IntegerField(help_text="Riigikogu koosseisu number")
    plenary_session = models.IntegerField(help_text="Istungjärgu number")
    date = models.DateTimeField(help_text="Istungi alguseaeg")
    title = models.TextField(help_text="Istungi pealkiri")
    title_en = models.TextField(blank=True, null=True, help_text="Istungi pealkiri inglise keeles")
    title_ru = models.TextField(blank=True, null=True, help_text="Istungi pealkiri vene keeles")
    edited = models.BooleanField(default=False, help_text="Kas stenogramm on toimetatud")
    is_incomplete = models.BooleanField(default=False, db_index=True,
                                       help_text="Kas istung sisaldab puudulikke stenogramme")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Plenary Session"
        verbose_name_plural = "Plenary Sessions"
        unique_together = ['membership', 'plenary_session', 'date']
        ordering = ['-date']
        
    def __str__(self):
        return f"{self.title} ({self.date.date()})"
    
    def get_localized_title(self, language='et', show_missing=False):
        """Get title in specified language, fallback to Estonian"""
        if language == 'en' and self.title_en:
            return self.title_en
        elif language == 'ru' and self.title_ru:
            return self.title_ru
        elif language != 'et' and show_missing and self.title:
            # Import here to avoid circular import
            from .translation import translate
            missing_text = translate('TRANSLATION_MISSING', language)
            return f"{missing_text}{self.title}"
        return self.title or ''


class AgendaItem(models.Model):
    """Model representing an agenda item in a plenary session"""
    
    uuid = models.CharField(max_length=36, unique=True, db_index=True,
                           help_text="Päevakorrapunkti UUID")
    plenary_session = models.ForeignKey(PlenarySession, on_delete=models.CASCADE,
                                      related_name='agenda_items')
    date = models.DateTimeField(help_text="Päevakorrapunkti aeg")
    title = models.TextField(help_text="Päevakorrapunkti pealkiri")
    title_en = models.TextField(blank=True, null=True, help_text="Päevakorrapunkti pealkiri inglise keeles")
    title_ru = models.TextField(blank=True, null=True, help_text="Päevakorrapunkti pealkiri vene keeles")
    total_time_seconds = models.IntegerField(blank=True, null=True, help_text="Kogu päevakorrapunkti kestus sekundites")
    is_incomplete = models.BooleanField(default=False, db_index=True,
                                       help_text="Kas päevakord sisaldab puudulikke stenogramme")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Agenda Item"
        verbose_name_plural = "Agenda Items"
        ordering = ['date']
        
    def __str__(self):
        return f"{self.title[:100]}..."
    
    @property
    def formatted_total_time(self):
        """Return formatted total time as hours:minutes"""
        if not self.total_time_seconds:
            return ""
        
        hours = self.total_time_seconds // 3600
        minutes = (self.total_time_seconds % 3600) // 60
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    
    def get_localized_title(self, language='et', show_missing=False):
        """Get title in specified language, fallback to Estonian"""
        if language == 'en' and self.title_en:
            return self.title_en
        elif language == 'ru' and self.title_ru:
            return self.title_ru
        elif language != 'et' and show_missing and self.title:
            # Import here to avoid circular import
            from .translation import translate
            missing_text = translate('TRANSLATION_MISSING', language)
            return f"{missing_text}{self.title}"
        return self.title or ''


class AgendaSummary(models.Model):
    """Model for storing structured AI-generated agenda summaries"""
    
    agenda_item = models.OneToOneField(AgendaItem, on_delete=models.CASCADE,
                                     related_name='structured_summary')
    summary_text = models.TextField(help_text="AI kokkuvõte päevakorrapunktist")
    summary_text_en = models.TextField(blank=True, null=True, help_text="AI kokkuvõte inglise keeles")
    summary_text_ru = models.TextField(blank=True, null=True, help_text="AI kokkuvõte vene keeles")
    is_incomplete = models.BooleanField(default=False, db_index=True,
                                       help_text="Kas kokkuvõte on puudulik (sisaldab puudulikke stenogramme)")
    
    # Raw XML response with decrypted IDs
    xml_response = models.TextField(blank=True, null=True, 
                                  help_text="Täielik XML vastus dekrüpteeritud ID-dega")
    
    # AI generation tracking
    ai_summary_generated_at = models.DateTimeField(blank=True, null=True,
                                                   help_text="AI kokkuvõtte genereerimise aeg")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Agenda Summary"
        verbose_name_plural = "Agenda Summaries"
        
    def __str__(self):
        return f"Summary for {self.agenda_item.title[:50]}..."
    
    def get_localized_summary(self, language='et', show_missing=False):
        """Get summary in specified language, fallback to Estonian"""
        if language == 'en' and self.summary_text_en:
            return self.summary_text_en
        elif language == 'ru' and self.summary_text_ru:
            return self.summary_text_ru
        elif language != 'et' and show_missing and self.summary_text:
            # Import here to avoid circular import
            from .translation import translate
            missing_text = translate('TRANSLATION_MISSING', language)
            return f"{missing_text}{self.summary_text}"
        return self.summary_text or ''


class AgendaDecision(models.Model):
    """Model for storing decisions made during agenda items"""
    
    agenda_item = models.ForeignKey(AgendaItem, on_delete=models.CASCADE,
                                  related_name='decisions')
    politician = models.ForeignKey(Politician, on_delete=models.CASCADE,
                                 related_name='agenda_decisions', 
                                 blank=True, null=True,
                                 help_text="Poliitik kes tegi otsuse (null kui kollektiivne)")
    decision_text = models.TextField(help_text="Otsuse kirjeldus")
    decision_text_en = models.TextField(blank=True, null=True, help_text="Otsuse kirjeldus inglise keeles")
    decision_text_ru = models.TextField(blank=True, null=True, help_text="Otsuse kirjeldus vene keeles")
    is_incomplete = models.BooleanField(default=False, db_index=True,
                                       help_text="Kas otsus on puudulik (sisaldab puudulikke stenogramme)")
    
    # AI generation tracking
    ai_summary_generated_at = models.DateTimeField(blank=True, null=True,
                                                   help_text="AI kokkuvõtte genereerimise aeg")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Agenda Decision"
        verbose_name_plural = "Agenda Decisions"
        ordering = ['created_at']
        
    def __str__(self):
        if self.politician:
            return f"Decision by {self.politician.full_name}: {self.decision_text[:50]}..."
        return f"Collective decision: {self.decision_text[:50]}..."
    
    def get_localized_decision(self, language='et', show_missing=False):
        """Get decision text in specified language, fallback to Estonian"""
        if language == 'en' and self.decision_text_en:
            return self.decision_text_en
        elif language == 'ru' and self.decision_text_ru:
            return self.decision_text_ru
        elif language != 'et' and show_missing and self.decision_text:
            # Import here to avoid circular import
            from .translation import translate
            missing_text = translate('TRANSLATION_MISSING', language)
            return f"{missing_text}{self.decision_text}"
        return self.decision_text or ''


class AgendaActivePolitician(models.Model):
    """Model for storing the most active politician in agenda items"""
    
    agenda_item = models.OneToOneField(AgendaItem, on_delete=models.CASCADE,
                                     related_name='active_politician')
    politician = models.ForeignKey(Politician, on_delete=models.CASCADE,
                                 related_name='active_in_agendas',
                                 blank=True, null=True,
                                 help_text="Aktiivne poliitik (null kui ei olnud eriti aktiivset)")
    activity_description = models.TextField(help_text="Aktiivsuse kirjeldus ja positsioon")
    activity_description_en = models.TextField(blank=True, null=True, help_text="Aktiivsuse kirjeldus inglise keeles")
    activity_description_ru = models.TextField(blank=True, null=True, help_text="Aktiivsuse kirjeldus vene keeles")
    is_incomplete = models.BooleanField(default=False, db_index=True,
                                       help_text="Kas aktiivsuse kirjeldus on puudulik (sisaldab puudulikke stenogramme)")
    
    # AI generation tracking
    ai_summary_generated_at = models.DateTimeField(blank=True, null=True,
                                                   help_text="AI kokkuvõtte genereerimise aeg")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Agenda Active Politician"
        verbose_name_plural = "Agenda Active Politicians"
        
    def __str__(self):
        if self.politician:
            return f"Active: {self.politician.full_name} in {self.agenda_item.title[:50]}..."
        return f"No active politician in {self.agenda_item.title[:50]}..."
    
    def get_localized_activity(self, language='et', show_missing=False):
        """Get activity description in specified language, fallback to Estonian"""
        if language == 'en' and self.activity_description_en:
            return self.activity_description_en
        elif language == 'ru' and self.activity_description_ru:
            return self.activity_description_ru
        elif language != 'et' and show_missing and self.activity_description:
            # Import here to avoid circular import
            from .translation import translate
            missing_text = translate('TRANSLATION_MISSING', language)
            return f"{missing_text}{self.activity_description}"
        return self.activity_description or ''


class Speech(models.Model):
    """Model representing a speech or statement by a politician"""
    
    EVENT_TYPES = (
        ('SPEECH', 'Speech'),
        ('VOTING_RESULT', 'Voting Result'),
        ('PRESENCE_CHECK', 'Presence Check'),
        ('SESSION_END', 'Session End'),
    )
    
    uuid = models.CharField(max_length=36, unique=True, db_index=True,
                           help_text="Sündmuse UUID")
    agenda_item = models.ForeignKey(AgendaItem, on_delete=models.CASCADE,
                                  related_name='speeches')
    politician = models.ForeignKey(Politician, on_delete=models.CASCADE,
                                 related_name='speeches', blank=True, null=True)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES, default='SPEECH',
                                help_text="Sündmuse tüüp")
    date = models.DateTimeField(help_text="Sündmuse aeg")
    speaker = models.CharField(max_length=200, help_text="Kõneleja nimi")
    text = models.TextField(help_text="Kõne või sõnavõtu tekst")
    link = models.URLField(blank=True, null=True, help_text="Link stenogrammile")
    ai_summary = models.TextField(blank=True, null=True, help_text="AI kokkuvõte kõnest")
    ai_summary_en = models.TextField(blank=True, null=True, help_text="AI kokkuvõte kõnest inglise keeles")
    ai_summary_ru = models.TextField(blank=True, null=True, help_text="AI kokkuvõte kõnest vene keeles")
    is_incomplete = models.BooleanField(default=False, db_index=True,
                                       help_text="Kas stenogramm on puudulik (koostamisel)")
    
    # AI generation and parsing tracking
    ai_summary_generated_at = models.DateTimeField(blank=True, null=True,
                                                   help_text="AI kokkuvõtte genereerimise aeg")
    parsed_at = models.DateTimeField(blank=True, null=True,
                                     help_text="Parsimise aeg API-st")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Speech"
        verbose_name_plural = "Speeches"
        ordering = ['-date']
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['speaker']),
            models.Index(fields=['event_type']),
        ]
        
    def __str__(self):
        return f"{self.speaker} - {self.date.date()} ({self.event_type})"
    
    @property
    def text_preview(self):
        """Return first 200 characters of the speech text"""
        if self.text:
            return self.text[:200] + "..." if len(self.text) > 200 else self.text
        return ""
    
    def get_localized_ai_summary(self, language='et', show_missing=False):
        """Get AI summary in specified language, fallback to Estonian"""
        if language == 'en' and self.ai_summary_en:
            return self.ai_summary_en
        elif language == 'ru' and self.ai_summary_ru:
            return self.ai_summary_ru
        elif language != 'et' and show_missing and self.ai_summary:
            # Import here to avoid circular import
            from .translation import translate
            missing_text = translate('TRANSLATION_MISSING', language)
            return f"{missing_text}{self.ai_summary}"
        return self.ai_summary or ''


# Obsolete models removed - replaced by PoliticianProfilePart system


class PoliticianProfilePart(models.Model):
    """Model for storing structured politician profile parts by period and category"""
    
    PROFILE_CATEGORIES = (
        ('POLITICAL_POSITION', 'Political Position'),
        ('TOPIC_EXPERTISE', 'Topic Expertise'),
        ('RHETORICAL_STYLE', 'Rhetorical Style'),
        ('ACTIVITY_PATTERNS', 'Activity Patterns'),
        ('OPPOSITION_STANCE', 'Opposition Stance'),
        ('COLLABORATION_STYLE', 'Collaboration Style'),
        ('REGIONAL_FOCUS', 'Regional Focus'),
        ('ECONOMIC_VIEWS', 'Economic Views'),
        ('SOCIAL_ISSUES', 'Social Issues'),
        ('LEGISLATIVE_FOCUS', 'Legislative Focus'),
    )
    
    PERIOD_TYPES = (
        ('AGENDA', 'Agenda Item'),
        ('PLENARY_SESSION', 'Plenary Session'),
        ('MONTH', 'Month'),
        ('YEAR', 'Year'),
        ('ALL', 'All Time'),
    )
    
    politician = models.ForeignKey(Politician, on_delete=models.CASCADE,
                                 related_name='profile_parts')
    category = models.CharField(max_length=50, choices=PROFILE_CATEGORIES,
                              help_text="Profilieerimise kategooria")
    period_type = models.CharField(max_length=20, choices=PERIOD_TYPES,
                                 help_text="Perioodi tüüp")
    
    # Period identifiers (only one should be filled based on period_type)
    agenda_item = models.ForeignKey(AgendaItem, on_delete=models.CASCADE,
                                  blank=True, null=True,
                                  related_name='profile_parts',
                                  help_text="Päevakorrapunkt (kui period_type=AGENDA)")
    plenary_session = models.ForeignKey(PlenarySession, on_delete=models.CASCADE,
                                      blank=True, null=True,
                                      related_name='profile_parts',
                                      help_text="Istung (kui period_type=PLENARY_SESSION)")
    month = models.CharField(max_length=7, blank=True, null=True,
                           help_text="Kuu formaadis MM.YYYY (kui period_type=MONTH)")
    year = models.IntegerField(blank=True, null=True,
                             help_text="Aasta (kui period_type=YEAR)")
    
    # AI-generated analysis
    analysis = models.TextField(help_text="AI analüüs eesti keeles")
    analysis_en = models.TextField(blank=True, null=True, 
                                 help_text="AI analüüs inglise keeles")
    analysis_ru = models.TextField(blank=True, null=True, 
                                 help_text="AI analüüs vene keeles")
    
    # Quantitative data (JSON format for flexibility)
    metrics = models.JSONField(default=dict, blank=True,
                             help_text="Kvantitiivsed mõõdikud JSON formaadis")
    
    
    # Data used for analysis
    speeches_analyzed = models.IntegerField(default=0,
                                          help_text="Analüüsitud kõnede arv")
    date_range_start = models.DateField(blank=True, null=True,
                                      help_text="Analüüsi perioodi algus")
    date_range_end = models.DateField(blank=True, null=True,
                                    help_text="Analüüsi perioodi lõpp")
    is_incomplete = models.BooleanField(default=False, db_index=True,
                                       help_text="Kas profiil on puudulik (sisaldab puudulikke stenogramme)")
    
    # AI generation tracking
    ai_summary_generated_at = models.DateTimeField(blank=True, null=True,
                                                   help_text="AI profiili genereerimise aeg")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Politician Profile Part"
        verbose_name_plural = "Politician Profile Parts"
        # Ensure unique combinations based on period type
        unique_together = [
            ['politician', 'category', 'period_type', 'agenda_item'],
            ['politician', 'category', 'period_type', 'plenary_session'],
            ['politician', 'category', 'period_type', 'month'],
            ['politician', 'category', 'period_type', 'year'],
        ]
        ordering = ['politician', 'category', 'period_type']
        
    def __str__(self):
        period_desc = self.get_period_description()
        return f"{self.politician.full_name} - {self.get_category_display()} ({period_desc})"
    
    def get_period_description(self):
        """Get human-readable period description"""
        if self.period_type == 'AGENDA' and self.agenda_item:
            return f"Agenda: {self.agenda_item.title[:50]}..."
        elif self.period_type == 'PLENARY_SESSION' and self.plenary_session:
            return f"Session: {self.plenary_session.title[:50]}..."
        elif self.period_type == 'MONTH' and self.month:
            return f"Month: {self.month}"
        elif self.period_type == 'YEAR' and self.year:
            return f"Year: {self.year}"
        elif self.period_type == 'ALL':
            return "All Time"
        return self.period_type
    
    def get_localized_analysis(self, language='et', show_missing=False):
        """Get analysis in specified language, fallback to Estonian"""
        if language == 'en' and self.analysis_en:
            return self.analysis_en
        elif language == 'ru' and self.analysis_ru:
            return self.analysis_ru
        elif language != 'et' and show_missing and self.analysis:
            # Import here to avoid circular import
            from .translation import translate
            missing_text = translate('TRANSLATION_MISSING', language)
            return f"{missing_text}{self.analysis}"
        return self.analysis
    
    def clean(self):
        """Validate that only appropriate period identifier is set"""
        from django.core.exceptions import ValidationError
        
        # Count non-null period identifiers
        period_fields = [self.agenda_item, self.plenary_session, self.month, self.year]
        non_null_count = sum(1 for field in period_fields if field is not None)
        
        if self.period_type == 'ALL':
            if non_null_count > 0:
                raise ValidationError("For period_type=ALL, no period identifiers should be set")
        elif self.period_type == 'AGENDA':
            if not self.agenda_item or non_null_count != 1:
                raise ValidationError("For period_type=AGENDA, only agenda_item should be set")
        elif self.period_type == 'PLENARY_SESSION':
            if not self.plenary_session or non_null_count != 1:
                raise ValidationError("For period_type=PLENARY_SESSION, only plenary_session should be set")
        elif self.period_type == 'MONTH':
            if not self.month or non_null_count != 1:
                raise ValidationError("For period_type=MONTH, only month should be set")
        elif self.period_type == 'YEAR':
            if not self.year or non_null_count != 1:
                raise ValidationError("For period_type=YEAR, only year should be set")


class MediaReaction(models.Model):
    """Model for storing media reactions and analysis for politician profiles"""
    
    politician = models.ForeignKey(Politician, on_delete=models.CASCADE,
                                 related_name='media_reactions')
    category = models.CharField(max_length=50, 
                              choices=PoliticianProfilePart.PROFILE_CATEGORIES,
                              help_text="Profilieerimise kategooria")
    
    # Media data from external sources
    total_mentions = models.IntegerField(default=0,
                                       help_text="Kogu mainimiste arv meedias")
    total_quotes = models.IntegerField(default=0,
                                     help_text="Kogu tsitaatide arv")
    avg_sentiment = models.FloatField(default=0.0,
                                    help_text="Keskmine meeleolu (-1 kuni 1)")
    
    # AI-generated analysis comparing media vs politician's position
    media_analysis_et = models.TextField(help_text="Meedia analüüs eesti keeles")
    media_analysis_en = models.TextField(blank=True, null=True,
                                       help_text="Meedia analüüs inglise keeles")
    media_analysis_ru = models.TextField(blank=True, null=True,
                                       help_text="Meedia analüüs vene keeles")
    
    # Summary for display at top of category analysis
    media_summary_et = models.TextField(help_text="Lühike meedia kokkuvõte eesti keeles")
    media_summary_en = models.TextField(blank=True, null=True,
                                      help_text="Lühike meedia kokkuvõte inglise keeles")
    media_summary_ru = models.TextField(blank=True, null=True,
                                      help_text="Lühike meedia kokkuvõte vene keeles")
    
    # Raw media data (JSON format)
    quotes_data = models.JSONField(default=list, blank=True,
                                 help_text="Tsitaadid JSON formaadis")
    sources_data = models.JSONField(default=list, blank=True,
                                  help_text="Allikad JSON formaadis")
    time_series_data = models.JSONField(default=list, blank=True,
                                      help_text="Ajaline andmestik JSON formaadis")
    
    # Metadata
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Media Reaction"
        verbose_name_plural = "Media Reactions"
        unique_together = ['politician', 'category']
        ordering = ['politician', 'category']
        
    def __str__(self):
        return f"{self.politician.full_name} - {self.get_category_display()} (Media)"
    
    def get_localized_analysis(self, language='et'):
        """Get media analysis in specified language, fallback to Estonian"""
        if language == 'en' and self.media_analysis_en:
            return self.media_analysis_en
        elif language == 'ru' and self.media_analysis_ru:
            return self.media_analysis_ru
        return self.media_analysis_et or ''
    
    def get_localized_summary(self, language='et'):
        """Get media summary in specified language, fallback to Estonian"""
        if language == 'en' and self.media_summary_en:
            return self.media_summary_en
        elif language == 'ru' and self.media_summary_ru:
            return self.media_summary_ru
        return self.media_summary_et or ''
    
    @property
    def sentiment_label(self):
        """Get human-readable sentiment label"""
        if self.avg_sentiment > 0.1:
            return "Positive"
        elif self.avg_sentiment < -0.1:
            return "Negative"
        else:
            return "Neutral"
    
    @property
    def unique_sources_count(self):
        """Get count of unique sources"""
        return len(self.sources_data) if self.sources_data else 0


class StatisticsEntry(models.Model):
    """Model for storing various system statistics with multilingual support"""
    
    name = models.CharField(max_length=200, unique=True, help_text="Statistika nimi eesti keeles")
    name_ru = models.CharField(max_length=200, blank=True, null=True, help_text="Statistika nimi vene keeles")
    name_en = models.CharField(max_length=200, blank=True, null=True, help_text="Statistika nimi inglise keeles")
    
    value = models.BigIntegerField(help_text="Statistika väärtus")
    percentage = models.FloatField(blank=True, null=True, help_text="Protsent (kui rakendub)")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Statistics Entry"
        verbose_name_plural = "Statistics Entries"
        ordering = ['name']
        
    def __str__(self):
        return f"{self.name}: {self.value}" + (f" ({self.percentage}%)" if self.percentage is not None else "")
    
    def get_localized_name(self, language='et'):
        """Get name in specified language, fallback to Estonian"""
        if language == 'en' and self.name_en:
            return self.name_en
        elif language == 'ru' and self.name_ru:
            return self.name_ru
        return self.name or ''


class TextPage(models.Model):
    """Model for storing static text pages with multilingual support"""
    
    slug = models.SlugField(max_length=100, unique=True, help_text="URL slug (e.g., 'about-us')")
    
    # Estonian content (default)
    title = models.CharField(max_length=200, help_text="Pealkiri eesti keeles")
    meta_description = models.TextField(max_length=300, blank=True, null=True, help_text="Meta kirjeldus eesti keeles")
    keywords = models.CharField(max_length=500, blank=True, null=True, help_text="Võtmesõnad eesti keeles")
    content = models.TextField(help_text="Sisu eesti keeles")
    
    # English content
    title_en = models.CharField(max_length=200, blank=True, null=True, help_text="Pealkiri inglise keeles")
    meta_description_en = models.TextField(max_length=300, blank=True, null=True, help_text="Meta kirjeldus inglise keeles")
    keywords_en = models.CharField(max_length=500, blank=True, null=True, help_text="Võtmesõnad inglise keeles")
    content_en = models.TextField(blank=True, null=True, help_text="Sisu inglise keeles")
    
    # Russian content
    title_ru = models.CharField(max_length=200, blank=True, null=True, help_text="Pealkiri vene keeles")
    meta_description_ru = models.TextField(max_length=300, blank=True, null=True, help_text="Meta kirjeldus vene keeles")
    keywords_ru = models.CharField(max_length=500, blank=True, null=True, help_text="Võtmesõnad vene keeles")
    content_ru = models.TextField(blank=True, null=True, help_text="Sisu vene keeles")
    
    # Settings
    is_published = models.BooleanField(default=True, help_text="Kas lehekülg on avaldatud")
    show_in_menu = models.BooleanField(default=False, help_text="Kas näidata menüüs")
    menu_order = models.IntegerField(default=0, help_text="Järjekord menüüs")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Text Page"
        verbose_name_plural = "Text Pages"
        ordering = ['menu_order', 'title']
        
    def __str__(self):
        return self.title
    
    def get_localized_title(self, language='et'):
        """Get title in specified language, fallback to Estonian"""
        if language == 'en' and self.title_en:
            return self.title_en
        elif language == 'ru' and self.title_ru:
            return self.title_ru
        return self.title or ''
    
    def get_localized_meta_description(self, language='et'):
        """Get meta description in specified language, fallback to Estonian"""
        if language == 'en' and self.meta_description_en:
            return self.meta_description_en
        elif language == 'ru' and self.meta_description_ru:
            return self.meta_description_ru
        return self.meta_description or ''
    
    def get_localized_keywords(self, language='et'):
        """Get keywords in specified language, fallback to Estonian"""
        if language == 'en' and self.keywords_en:
            return self.keywords_en
        elif language == 'ru' and self.keywords_ru:
            return self.keywords_ru
        return self.keywords or ''
    
    def get_localized_content(self, language='et'):
        """Get content in specified language, fallback to Estonian"""
        if language == 'en' and self.content_en:
            return self.content_en
        elif language == 'ru' and self.content_ru:
            return self.content_ru
        return self.content or ''


class ParliamentParseError(models.Model):
    """Model for tracking parsing errors from the Parliament API"""
    
    ERROR_TYPES = (
        ('API_CONNECTION', 'API Connection Error'),
        ('DATA_PARSING', 'Data Parsing Error'),
        ('MISSING_DATA', 'Missing Required Data'),
        ('MISSING_STENOGRAM', 'Missing Stenogram'),
        ('VALIDATION', 'Data Validation Error'),
        ('DATABASE', 'Database Error'),
        ('PHOTO_DOWNLOAD', 'Photo Download Error'),
        ('OTHER', 'Other Error'),
    )
    
    error_type = models.CharField(max_length=50, choices=ERROR_TYPES, default='OTHER',
                                 help_text="Vea tüüp")
    error_message = models.TextField(help_text="Veateade")
    error_details = models.TextField(blank=True, null=True, 
                                    help_text="Täiendavad detailid JSON formaadis")
    
    # Context information
    entity_type = models.CharField(max_length=50, blank=True, null=True,
                                  help_text="Üksuse tüüp (politician, session, agenda, speech)")
    entity_id = models.CharField(max_length=200, blank=True, null=True,
                                help_text="Üksuse identifikaator (UUID või ID)")
    entity_name = models.CharField(max_length=500, blank=True, null=True,
                                  help_text="Üksuse nimi või pealkiri")
    
    # Year for grouping transparency reports
    year = models.IntegerField(blank=True, null=True, db_index=True,
                              help_text="Aasta, millega see viga on seotud")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        verbose_name = "Parliament Parse Error"
        verbose_name_plural = "Parliament Parse Errors"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['error_type']),
        ]
        
    def __str__(self):
        return f"{self.get_error_type_display()}: {self.error_message[:100]}"
